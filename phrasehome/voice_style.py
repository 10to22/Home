"""The voice modeler: an accent, and a mood for every line the house says.

    voice:
      engine: melo          # or say (Mac), chatterbox, http (a voice server on your LAN), piper, espeak
      accent: en_AU         # picks an Australian voice where the engine has one
      emotion: true         # each line gets a mood: cheeky, annoyed, cheerful, calm, urgent, soft, clear
      emotions: { timer_done: annoyed }   # your own mood for a phrase key

How a line's mood is picked (first match wins):
1. Safety lines (smoke, CO, leak, weather, panic, the alarm's warnings) are always `clear`: slower,
   steady, louder, no voice effect. Nothing can override that.
2. `speak(..., emotion="cheeky")` from code, or `{text: ..., emotion: ...}` in a phrase.
3. A tag at the start of the text: "[cheeky] Lights off. Try not to trip." (the AI adds these;
   tags are never read aloud).
4. `voice: emotions: {key: mood}` in config, then the built-in mood of that phrase key.
5. A guess from the words, or the style's usual mood (cheeky for the mean and Aussie styles).
Then, in a room during its quiet hours, anything that isn't safety or urgent is said `soft`.

Each mood is a handful of knobs (rate, pitch, pauses, liveliness, loudness, exaggeration) that every
engine turns into what it understands: Piper's length/noise scales and sentence silence, eSpeak's
speed, pitch and word gaps, the Mac's rate and silences, MeloTTS's speed and noise, Chatterbox's
exaggeration and cfg weight, and instructions for an HTTP voice server. Pitch and loudness the engine
can't do itself are applied afterwards with ffmpeg. Tune them in `phrasehome voice tune` or on the
dashboard's Voice page (saved to data/voice_tuning.json).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.voice_style")

# the knobs, their neutral value and the range the tuner allows
KNOBS: dict[str, tuple[float, float, float]] = {
    "rate": (1.0, 0.6, 1.6),          # speaking speed (x the voice's own speed)
    "pitch": (0.0, -6.0, 6.0),        # semitones up or down
    "pause": (1.0, 0.3, 3.0),         # gaps between sentences and words (x normal)
    "lively": (1.0, 0.2, 2.0),        # pitch movement / variation (flat < 1 < animated)
    "volume": (0.0, -12.0, 12.0),     # dB louder or quieter
    "exaggeration": (0.5, 0.25, 2.0),  # neural engines (Chatterbox): how much emotion to act out
}

EMOTIONS: dict[str, dict[str, Any]] = {
    # strong enough to hear on any engine (Mac voices, Piper, eSpeak): speed, pitch, pauses, loudness
    "neutral": {"about": "plain and even"},
    "calm": {"rate": 0.88, "pitch": -1.0, "pause": 1.4, "lively": 0.7, "volume": -1.5, "exaggeration": 0.35,
             "about": "relaxed and warm, slower"},
    "clear": {"rate": 0.86, "pitch": 0.0, "pause": 1.5, "lively": 0.6, "volume": 2.0, "exaggeration": 0.3,
              "about": "safety announcements: slow, steady and easy to understand"},
    "cheeky": {"rate": 1.08, "pitch": 1.5, "pause": 0.85, "lively": 1.5, "exaggeration": 0.9,
               "about": "playful, a grin in the voice"},
    "annoyed": {"rate": 1.12, "pitch": -2.5, "pause": 0.75, "lively": 1.3, "volume": 2.0, "exaggeration": 1.0,
                "about": "exasperated, clipped"},
    "cheerful": {"rate": 1.08, "pitch": 3.0, "pause": 0.9, "lively": 1.5, "volume": 1.0, "exaggeration": 0.85,
                 "about": "bright and upbeat"},
    "urgent": {"rate": 1.16, "pitch": 1.5, "pause": 0.7, "lively": 1.2, "volume": 3.5, "exaggeration": 0.8,
               "about": "quick and alert"},
    "soft": {"rate": 0.9, "pitch": -1.5, "pause": 1.25, "lively": 0.7, "volume": -5.0, "exaggeration": 0.35,
             "about": "quiet-hours voice: low and gentle"},
}

# what a voice server that takes written instructions (OpenAI-style `instructions`) is told
INSTRUCTIONS = {
    "neutral": "Speak naturally and evenly.",
    "calm": "Speak calmly and warmly, a little slower than usual.",
    "clear": "Speak slowly, steadily and very clearly, like a calm safety announcement. No jokes in the voice.",
    "cheeky": "Speak with a cheeky, playful, teasing tone, as if grinning.",
    "annoyed": "Speak in an exasperated, mildly annoyed, clipped tone.",
    "cheerful": "Speak in a bright, cheerful, upbeat tone.",
    "urgent": "Speak quickly and urgently, alert but not panicked.",
    "soft": "Speak softly and gently, as if it's late at night and people are sleeping.",
}

# words the AI (or anyone) might use for a mood -> the mood
ALIASES = {"happy": "cheerful", "excited": "cheerful", "upbeat": "cheerful", "warm": "calm",
           "playful": "cheeky", "sarcastic": "cheeky", "smug": "cheeky", "teasing": "cheeky", "dry": "neutral",
           "angry": "annoyed", "irritated": "annoyed", "exasperated": "annoyed", "grumpy": "annoyed",
           "serious": "clear", "safety": "clear", "alert": "urgent", "quiet": "soft", "sleepy": "soft",
           "gentle": "soft", "whisper": "soft", "plain": "neutral", "flat": "neutral"}

# safety lines: always `clear`, never a voice effect, whatever else says otherwise
SAFETY_KEYS = {"smoke_alarm", "co_alarm", "leak_alarm", "weather_alert", "weather_warning", "panic",
               "alarm_entry", "alarm_alert"}

KEY_EMOTIONS = {
    **dict.fromkeys(["wake_nag", "wake_snooze_denied", "wake_back_to_bed", "wake_still_in_bed", "wake_gave_up",
                     "fingerprint_rejected", "fingerprint_lockout", "retinal_scan_failed", "access_denied",
                     "door_auto_locked", "door_left_open", "didnt_catch"], "annoyed"),
    **dict.fromkeys(["welcome_home", "welcome_person", "retinal_scan_complete", "fingerprint_accepted",
                     "alarm_disarmed", "good_morning", "wake_up", "package_delivered", "freak_off", "mail"],
                    "cheerful"),
    **dict.fromkeys(["goodnight", "lockdown", "privacy_on", "dnd_on", "confirm_ask", "confirm_cancelled",
                     "fingerprint_prompt"], "calm"),
    **dict.fromkeys(["package_taken", "device_offline"], "urgent"),
}
from .house_lines import MOODS as _HOUSE_MOODS, SAFETY as _HOUSE_SAFETY  # noqa: E402

KEY_EMOTIONS.update({k: v for k, v in _HOUSE_MOODS.items() if k not in KEY_EMOTIONS})
SAFETY_KEYS |= _HOUSE_SAFETY

# the usual mood of a style's lines when nothing more specific is known
STYLE_MOOD = {"mean": "cheeky", "aussie": "cheeky", "aussie_uncut": "cheeky", "deadpan": "neutral"}

ACCENTS: dict[str, dict[str, Any]] = {
    "en_AU": {"name": "Australian", "mac": ["Lee", "Karen", "Matilda", "Catherine", "Gordon"],
              "melo": "EN-AU", "espeak": "en-gb", "piper_prefix": ["en_AU", "en_GB"],
              "instructions": "Speak with a natural, relaxed Australian accent."},
    "en_GB": {"name": "British", "mac": ["Daniel", "Kate", "Serena", "Oliver"], "melo": "EN-BR",
              "espeak": "en-gb", "piper_prefix": ["en_GB"], "instructions": "Speak with a natural British accent."},
    "en_US": {"name": "American", "mac": ["Samantha", "Alex", "Ava", "Tom"], "melo": "EN-US",
              "espeak": "en-us", "piper_prefix": ["en_US"], "instructions": "Speak with a natural American accent."},
    "en_IN": {"name": "Indian", "mac": ["Rishi", "Veena"], "melo": "EN_INDIA", "espeak": "en-gb",
              "piper_prefix": [], "instructions": "Speak with a natural Indian English accent."},
}
ACCENT_NAMES = {"au": "en_AU", "aussie": "en_AU", "australian": "en_AU", "en-au": "en_AU", "gb": "en_GB",
                "uk": "en_GB", "british": "en_GB", "en-gb": "en_GB", "us": "en_US", "american": "en_US",
                "en-us": "en_US", "in": "en_IN", "indian": "en_IN", "en-in": "en_IN"}

_MOODS = "|".join(sorted(set(EMOTIONS) | set(ALIASES), key=len, reverse=True))
TAG = re.compile(rf"(?:\[|<)\s*(?:mood\s*[:=]\s*)?({_MOODS})\s*(?:\]|>)", re.I)
LEADING_TAG = re.compile(rf"^\s*{TAG.pattern}\s*", re.I)

# a guess from the words, for free text (the AI's answers, intercom) without a tag
_GUESS = [  # for sentences the AI didn't tag: the wording gives the feeling away
    ("clear", re.compile(r"\b(warning|emergency|danger|fire|smoke|carbon monoxide|gas leak|evacuate|get out|"
                         r"call (?:000|911|112|triple zero))\b", re.I)),
    ("urgent", re.compile(r"\b(right now|immediately|hurry|quick(?:ly)?|asap|straight away|don't wait|"
                          r"leaving in \d+|it's late|you're late|running late|last chance)\b", re.I)),
    ("calm", re.compile(r"\b(sorry to hear|i'm sorry|don't worry|it's okay|it's ok|take care|you're safe|breathe|"
                        r"no rush|take your time|deep breath|that sounds hard|i'm here|rest up|feel better|"
                        r"sleep well|good night|goodnight|easy does it|relax)\b", re.I)),
    ("annoyed", re.compile(r"\b(again[.?!]|again,|seriously|for fuck'?s sake|how many times|ugh|still in bed|"
                           r"i already told you|honestly|bloody|for god'?s sake|you left|left (?:it|the \w+) "
                           r"(?:on|open|unlocked)|nobody|no one|every single|unbelievable|typical|of course you|"
                           r"lazy|bludger|drongo|galah|get up|wake up|enough|stop it|piss off|oi\b)", re.I)),
    ("cheerful", re.compile(r"\b(great news|good news|congrat\w*|happy birthday|well done|ripper|beauty|"
                            r"awesome|brilliant|lovely|nice one|nailed it|good on ya|fantastic|amazing|yay|"
                            r"sunny|perfect|welcome home|good morning|party|weekend|let's go|cheers|legend)\b",
                            re.I)),
    ("cheeky", re.compile(r"\b(technically|apparently|surely|i suppose|i guess|might as well|not that|"
                          r"don't thank me|you're welcome|no pressure|just saying|bold move|cute)\b", re.I)),
]


def mood_name(word: Any) -> str | None:
    """'Happy' -> 'cheerful'; unknown -> None."""
    if not word:
        return None
    w = str(word).strip().lower()
    w = ALIASES.get(w, w)
    return w if w in EMOTIONS else None


def accent_name(accent: Any) -> str | None:
    if not accent:
        return None
    a = str(accent).strip()
    a = ACCENT_NAMES.get(a.lower(), a)
    a = a.replace("-", "_")
    if "_" in a:
        lang, region = a.split("_", 1)
        a = f"{lang.lower()}_{region.upper()}"
    return a if a in ACCENTS else None


def split_tag(text: str) -> tuple[str | None, str]:
    """'[cheeky] Lights off.' -> ('cheeky', 'Lights off.'). Other tags in the text are removed too."""
    m = LEADING_TAG.match(text or "")
    mood = mood_name(m.group(1)) if m else None
    rest = text[m.end():] if m else (text or "")
    rest = TAG.sub("", rest)
    return mood, re.sub(r"\s{2,}", " ", rest).strip()


def strip_tags(text: str) -> str:
    return split_tag(text)[1]


def guess(text: str) -> str | None:
    for mood, rx in _GUESS:
        if rx.search(text or ""):
            return mood
    return None


def _clamp(knob: str, value: Any) -> float:
    _, lo, hi = KNOBS[knob]
    return round(min(hi, max(lo, float(value))), 3)


class VoiceModeler:
    """Accent and mood settings for the house's voice, with the user's tuning on top."""

    def __init__(self, config) -> None:
        self.config = config
        cfg = config.voice or {}
        self.enabled = self.config_enabled = bool(cfg.get("emotion", True))  # on unless `emotion: false`
        self.accent = self.config_accent = accent_name(cfg.get("accent"))
        if cfg.get("accent") and not self.accent:
            log.warning("unknown voice accent %r (have: %s)", cfg.get("accent"), ", ".join(ACCENTS))
        style = cfg.get("style", "mean")
        self.style_mood = STYLE_MOOD.get(style or "", "neutral")
        self.key_moods = {k: m for k, v in (cfg.get("emotions") or {}).items() if (m := mood_name(v))}
        self.safety_effects = bool(cfg.get("safety_effects", False))
        self.path = Path(config.resolve_path(config.data_dir)) / "voice_tuning.json"
        self._lock = threading.Lock()
        self.tuning: dict[str, dict[str, float]] = {}
        self.load()

    # ---------- tuning ----------
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        self.tuning = {m: {k: _clamp(k, v) for k, v in (knobs or {}).items() if k in KNOBS}
                       for m, knobs in (data.get("emotions") or {}).items() if m in EMOTIONS}
        # switched on the dashboard: wins over config.yaml until reset
        self.options = {k: data[k] for k in ("enabled", "accent") if k in data}
        self.enabled = bool(self.options.get("enabled", self.config_enabled))
        self.accent = (accent_name(self.options["accent"]) if self.options.get("accent")
                       else None if "accent" in self.options else self.config_accent)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({**self.options, "emotions": self.tuning}, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def tune(self, mood: str, **knobs: Any) -> dict[str, float]:
        """Change some knobs of one mood (saved). Returns that mood's knobs now."""
        name = mood_name(mood)
        if not name:
            raise KeyError(f"no mood called {mood!r} (have: {', '.join(EMOTIONS)})")
        with self._lock:
            mine = dict(self.tuning.get(name, {}))
            for k, v in knobs.items():
                if k not in KNOBS:
                    raise KeyError(f"no knob called {k!r} (have: {', '.join(KNOBS)})")
                if v is not None:
                    mine[k] = _clamp(k, v)
            self.tuning[name] = mine
            self._save()
        return self.knobs(name)

    def set_options(self, enabled: Any = None, accent: Any = "keep") -> None:
        """Turn moods on/off or change the accent (saved; wins over config.yaml). accent None = none,
        "config" = back to config.yaml's."""
        with self._lock:
            if enabled is not None:
                self.options["enabled"] = self.enabled = bool(enabled)
            if accent != "keep":
                if accent == "config":
                    self.options.pop("accent", None)
                    self.accent = self.config_accent
                else:
                    name = accent_name(accent) if accent else None
                    if accent and not name:
                        raise KeyError(f"no accent called {accent!r} (have: {', '.join(ACCENTS)})")
                    self.options["accent"] = name
                    self.accent = name
            self._save()

    def reset(self, mood: str | None = None) -> None:
        with self._lock:
            if mood is None:
                self.tuning, self.options = {}, {}
                self.enabled, self.accent = self.config_enabled, self.config_accent
            else:
                self.tuning.pop(mood_name(mood) or mood, None)
            self._save()

    def knobs(self, mood: str | None) -> dict[str, float]:
        """Every knob for a mood: neutral values, the built-in mood, then the user's tuning."""
        name = mood_name(mood) or "neutral"
        out = {k: d for k, (d, _, _) in KNOBS.items()}
        out.update({k: v for k, v in EMOTIONS[name].items() if k in KNOBS})
        out.update(self.tuning.get(name, {}))
        return out

    def describe(self) -> dict[str, Any]:
        """For the dashboard and `voice tune`: every mood with its knobs."""
        return {"enabled": self.enabled, "accent": self.accent, "config": {"enabled": self.config_enabled,
                                                                           "accent": self.config_accent},
                "accents": {k: a["name"] for k, a in ACCENTS.items()},
                "knobs": {k: {"default": d, "min": lo, "max": hi} for k, (d, lo, hi) in KNOBS.items()},
                "emotions": {m: {"about": e.get("about", ""), "knobs": self.knobs(m),
                                 "tuned": sorted(self.tuning.get(m, {}))} for m, e in EMOTIONS.items()}}

    # ---------- picking a mood ----------
    def pick(self, key: str | None, text: str, explicit: Any = None, phrase_mood: Any = None,
             tagged: Any = None, quiet: bool = False) -> str | None:
        """The mood for one line, or None when moods are off (safety lines are `clear` regardless
        of everything else, but only when moods are on)."""
        if not self.enabled:
            return None
        if key in SAFETY_KEYS:
            return "clear"
        mood = (mood_name(explicit) or mood_name(phrase_mood) or mood_name(tagged)
                or (self.key_moods.get(key) if key else None) or (KEY_EMOTIONS.get(key) if key else None)
                or guess(text) or self.style_mood)
        if quiet and mood not in ("clear", "urgent"):
            return "soft"
        return mood

    def wants_effect(self, mood: str | None) -> bool:
        """Voice effects (synthetic, robot...) are left off safety lines so they stay easy to hear."""
        return mood != "clear" or self.safety_effects

    def instructions(self, mood: str | None) -> str:
        parts = []
        if self.accent:
            parts.append(ACCENTS[self.accent]["instructions"])
        if mood:
            parts.append(INSTRUCTIONS.get(mood, ""))
        return " ".join(p for p in parts if p)

    def prompt_hint(self) -> str:
        """Told to the AI so it marks the mood of its answers (only when moods are on)."""
        if not self.enabled:
            return ""
        moods = ", ".join(f"[{m}]" for m in EMOTIONS if m not in ("clear", "soft"))
        return (f"\nYour voice acts out a mood, so always start your reply with a mood tag, and put a new tag "
                f"before any sentence whose feeling changes: {moods}. Example: \"[annoyed] You left the door "
                f"open again. [cheeky] Lucky I'm here.\" Pick the feeling that fits; don't use the same one every "
                f"time. Use [calm] for anything "
                f"about safety, health, emergencies or someone who is upset. The tag is not read aloud.\n")

    # ---------- what each engine is told ----------
    def engine_params(self, engine: str, mood: str | None, speed: float = 1.0,
                      knobs: dict | None = None) -> dict[str, Any]:
        """The knobs of a mood, turned into what one engine understands. `post` says what ffmpeg
        still has to do afterwards (pitch in semitones, volume in dB, tempo). knobs: try settings
        out without saving them (the dashboard's preview)."""
        k = self.knobs(mood)
        k.update({n: _clamp(n, x) for n, x in (knobs or {}).items() if n in KNOBS and x is not None})
        rate, pitch, pause, lively, vol, exag = (k["rate"], k["pitch"], k["pause"], k["lively"], k["volume"],
                                                 k["exaggeration"])
        out: dict[str, Any] = {"mood": mood, "knobs": k, "post": {"pitch": pitch, "volume": vol, "tempo": 1.0}}
        if engine == "piper":
            out.update(length_scale=round(1 / (speed * rate), 3),
                       noise_scale=round(min(1.2, max(0.1, 0.667 * lively)), 3),
                       noise_w=round(min(1.4, max(0.1, 0.8 * lively)), 3),
                       sentence_silence=round(0.2 * pause, 3))
        elif engine == "say":
            out.update(rate=int(180 * speed * rate), extra_silence_ms=int(max(0.0, pause - 1.0) * 400))
        elif engine == "espeak":
            out.update(speed=int(170 * speed * rate), pitch=int(min(99, max(0, 50 + 4 * pitch))),
                       gap=int(max(0.0, (pause - 1.0) * 10)), amplitude=int(min(200, max(10, 100 * 10 ** (vol / 20)))))
            out["post"] = {"pitch": 0.0, "volume": 0.0, "tempo": 1.0}
        elif engine == "melo":
            out.update(speed=round(speed * rate, 3), noise_scale=round(min(1.2, max(0.1, 0.6 * lively)), 3),
                       noise_scale_w=round(min(1.4, max(0.1, 0.8 * lively)), 3),
                       sdp_ratio=round(min(1.0, max(0.0, 0.2 * lively)), 3))
        elif engine == "chatterbox":
            # more exaggeration talks faster; a lower cfg weight slows it back down and lets it act
            out.update(exaggeration=exag, cfg_weight=round(min(0.8, max(0.2, 0.5 - 0.25 * (exag - 0.5))), 3))
            out["post"]["tempo"] = round(speed * rate, 3)
        elif engine == "http":
            out.update(speed=round(speed * rate, 3), base_speed=speed, instructions=self.instructions(mood),
                       exaggeration=exag,
                       cfg_weight=round(min(0.8, max(0.2, 0.5 - 0.25 * (exag - 0.5))), 3))
        else:  # command, or anything else: placeholders, and ffmpeg for tempo
            out.update(rate=round(speed * rate, 3))
        return out

    # ---------- the accent, per engine ----------
    def mac_voice(self, installed: dict[str, str]) -> str | None:
        """An installed Mac voice with this accent: {name: locale} -> name ('Lee', 'Karen'...)."""
        if not self.accent:
            return None
        locale = self.accent
        have = [n for n, loc in installed.items() if loc.replace("-", "_").lower() == locale.lower()]
        for want in ACCENTS[self.accent]["mac"]:
            for tier in ("premium", "enhanced", ""):
                for n in have:
                    if n.lower().split(" (")[0] == want.lower() and tier in n.lower():
                        return n
        return have[0] if have else None

    def piper_model(self, folder: Path) -> Path | None:
        """A downloaded Piper model with this accent (Piper has no Australian voice yet, so en_AU
        falls back to a British one, the closest)."""
        if not self.accent or not folder.is_dir():
            return None
        for prefix in ACCENTS[self.accent]["piper_prefix"]:
            found = sorted(folder.glob(f"{prefix}-*.onnx"))
            if found:
                return found[0]
        return None
