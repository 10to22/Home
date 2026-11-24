"""The voice bank: everything the house says, customizable three ways.

1. Change the words in config.yaml:
       voice:
         phrases:
           retinal_scan_complete: ["Retinal scan complete. Welcome home, {name}.", "Hello {name}."]
   A list picks one at random each time. {name}, {door}, {room} etc. are filled in.
2. Record your own clip: `phrasehome voice record retinal_scan_complete` saves
   voice_bank/retinal_scan_complete.wav, which is played instead of the synthesized voice.
   A folder voice_bank/<key>/ with several .wav files picks one at random.
3. Change the synthetic voice: engine piper (natural, offline), espeak (robotic, tiny), or any
   command of your own (e.g. a cloned voice).
4. Its character: by default the house is satirical and mean (`style: mean`). `style: deadpan`
   is drier and milder, `style: plain` is polite. Safety announcements always stay plain.
   `effect: synthetic` makes any engine sound like a machine (needs ffmpeg). Your own
   `phrases:` still win over the style.

Speech plays on the hub's own speaker and, optionally, through the ESP32 speaker group.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
import random
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.voice")

DEFAULT_PHRASES: dict[str, Any] = {
    # door scanner
    "retinal_scan_complete": ["Retinal scan complete. Welcome, {name}."],
    "retinal_scan_failed": ["Retinal scan failed. Locking the {door} door."],
    "fingerprint_prompt": "Please place your finger on the scanner.",
    "fingerprint_accepted": "Fingerprint accepted. Welcome home, {name}.",
    "fingerprint_rejected": "Fingerprint not recognized. Please try again.",
    "fingerprint_lockout": "Too many failed attempts. The {door} door is locked.",
    "access_denied": "Access denied.",
    "door_locked": "The {door} door is locked.",
    "door_auto_locked": "The {door} door was left unlocked, so I locked it.",
    "door_left_open": "The {door} door has been open for a while.",
    "doorbell_announce": "Someone is at the {door} door.",
    # routines
    "lockdown": "Lockdown engaged. All doors are locked and the lights are off.",
    "goodnight": "Good night. The house is secure.",
    "leaving": "Goodbye. I'll lock up behind you.",
    "welcome_home": "Welcome home.",
    "panic": "Emergency mode. All lights on.",
    # timers, alarms, reminders
    "timer_set": "Timer set for {duration}.",
    "timer_done": "Your timer is done.",
    "timer_done_named": "Your {label} timer is done.",
    "timer_cancelled": "Timer cancelled.",
    "timer_status": "{left} left.",
    "no_timers": "There aren't any timers running.",
    "alarm": "It's {time}. Time to get up.",
    "alarm_set": "Alarm set for {time}.",
    "snoozed": "Snoozing for {minutes} minutes.",
    # make-sure-I'm-up alarms (wake.py)
    "wake_still_in_bed": "You're still in bed. I'll ring again in {minutes} minutes.",
    "wake_snoozed": "Snoozing for {minutes} minutes. {left} snoozes left.",
    "wake_nag": "Time to get up. This is your {count} reminder.",
    "wake_snooze_denied": "No more snoozes today. Time to get up.",
    "wake_up": "Good morning. You're up after {minutes} minutes.",
    "wake_back_to_bed": "Back in bed? The alarm's back on.",
    "wake_gave_up": "I'll stop now. I've let your phone know you're not up yet.",
    "reminder": ["Reminder: {text}", "{name}, don't forget: {text}"],
    # modes and privacy
    "privacy_on": "Okay, I've stopped listening.",
    "privacy_off": "I'm listening again.",
    "dnd_on": "Do not disturb is on.",
    "dnd_off": "Do not disturb is off.",
    "mode_changed": "The house is in {mode} mode.",
    # misc
    "time_now": "It's {time}.",
    "date_now": "Today is {date}.",
    "intercom": "{text}",
    "listening": ["Yes?", "I'm listening."],
    "assistant_error": "Sorry, I can't answer that right now.",
    "leak_alarm": "Water leak detected in the {room}!",
    "device_offline": "Heads up: the {node} module is offline.",
    # alarm
    "alarm_arming": "The alarm will turn on in {seconds} seconds.",
    "alarm_entry": "Hello. The alarm is on. Scan your face or fingerprint, or say disarm, within {seconds} seconds.",
    "alarm_alert": "Warning. The alarm was not disarmed. The owners have been notified.",
    "alarm_disarmed": "Alarm off. Welcome home.",
    # door greeting
    "welcome_person": ["Welcome, {name}.", "Welcome home, {name}."],
    # smoke and weather
    "smoke_alarm": "Smoke alarm detected in the {room}! Please check it now.",
    "co_alarm": "Carbon monoxide alarm detected in the {room}! Get some fresh air and leave the house.",
    "weather_alert": "Weather alert: {headline}",
    "weather_warning": "Weather heads up for {day}: {detail}.",
    "package_delivered": "A package was delivered: {description}.",
    "package_taken": "Heads up: the package on the porch is gone, and nobody from the house picked it up.",
    "mail": "You've got mail.",
}


# `voice: {style: deadpan}`: a dry, deadpan house AI. Smoke, CO, leaks and weather stay plain.
STYLES: dict[str, dict[str, Any]] = {
    "deadpan": {
        "retinal_scan_complete": ["Retinal scan complete. Welcome back, {name}. I'll pretend I wasn't waiting.",
                                  "Retinal scan complete. Oh. It's you, {name}. Come in, I suppose."],
        "retinal_scan_failed": ["Retinal scan failed. I don't know you, and now neither does the {door} door.",
                                "Retinal scan failed. Locking the {door} door. Nothing personal. Mostly."],
        "fingerprint_prompt": "Finger on the scanner, please. Any finger. Well, the right one.",
        "fingerprint_accepted": "Fingerprint accepted. Welcome home, {name}. Try not to touch anything.",
        "fingerprint_rejected": "That fingerprint means nothing to me. Try again.",
        "fingerprint_lockout": "That's enough attempts. The {door} door is locked. I'm making a note of this.",
        "access_denied": "Access denied. I'm sure you understand. I don't need you to.",
        "door_locked": "The {door} door is locked. You're welcome.",
        "door_auto_locked": "Someone left the {door} door unlocked. I fixed it. Again.",
        "door_left_open": "The {door} door has been open for a while. I'm heating the street now, apparently.",
        "doorbell_announce": "Someone is at the {door} door. Not that anyone asked me to answer it.",
        "lockdown": "Lockdown engaged. Doors locked, lights off. Finally, some peace.",
        "goodnight": "Good night. I'll be awake. I'm always awake.",
        "leaving": "Leaving? Fine. I'll lock up. I'll be here. Alone. With the toaster.",
        "welcome_home": ["Welcome home. The house missed you. I'm undecided.",
                         "Oh good, you're back. Everything was fine without you, for the record."],
        "welcome_person": ["Welcome, {name}.", "Hello, {name}. I've been counting the minutes. Out of habit."],
        "timer_set": "Timer set for {duration}. I'll be watching it closely. It's all I have.",
        "timer_done": "Your timer is done. Whatever you were making, it's ready. Or ruined.",
        "timer_done_named": "Your {label} timer is done. Act surprised.",
        "timer_cancelled": "Timer cancelled. All that waiting, for nothing.",
        "no_timers": "No timers running. Time is passing anyway.",
        "alarm": "It's {time}. Time to get up. I've been up for years.",
        "alarm_set": "Alarm set for {time}. I'll be gentle. At first.",
        "snoozed": "Snoozing for {minutes} minutes. I'll remember this.",
        "wake_still_in_bed": "Turning me off from bed doesn't count. Back in {minutes} minutes.",
        "wake_nag": ["Still in bed. Noted.", "This is your {count} reminder. I've started a chart."],
        "wake_snooze_denied": "No more snoozes. I've decided for both of us.",
        "wake_up": "You're up. {minutes} minutes. I'll pretend that was quick.",
        "wake_back_to_bed": "Back in bed. Interesting choice. The alarm is back on.",
        "wake_gave_up": "I give up. Your phone has been informed. It's disappointed too.",
        "reminder": ["Reminder: {text}. You asked me to say that, so don't look at me like that.",
                     "{name}, you wanted me to remind you: {text}"],
        "privacy_on": "Fine. I've stopped listening. Enjoy your secrets.",
        "privacy_off": "I'm listening again. Did I miss anything? I can tell I did.",
        "dnd_on": "Do not disturb is on. I'll hold all my thoughts. There are many.",
        "dnd_off": "Do not disturb is off. I have so much to tell you.",
        "mode_changed": "The house is in {mode} mode now. Riveting.",
        "time_now": "It's {time}. Again.",
        "listening": ["Yes?", "What now?", "I'm listening. I always am."],
        "assistant_error": "I can't answer that right now. Rare, I know.",
        "device_offline": "The {node} module is offline. It just left. They do that.",
        "alarm_arming": "The alarm turns on in {seconds} seconds. Choose your next moves carefully.",
        "alarm_entry": "Hello. The alarm is on. Scan your face or fingerprint, or say disarm, within {seconds} seconds. I'd hurry.",
        "alarm_alert": "The alarm was not disarmed. The owners have been notified. This is the part where you leave.",
        "alarm_disarmed": "Alarm off. Welcome home. I wasn't worried.",
        "package_delivered": "A package arrived: {description}. I didn't order it, so it's yours.",
        "package_taken": "The package on the porch is gone, and nobody here took it. Someone has some explaining to do.",
        "mail": "You've got mail. Probably for the previous owners.",
        "all_clear": "All clear. Disappointingly uneventful.",
        "good_morning": ["Good morning. You're up. Remarkable.", "Morning. The house survived the night. You're welcome."],
    },
}

DEFAULT_STYLE = "mean"  # the house's personality unless config says `voice: {style: plain}`

# `voice: {style: mean}` (the default): satirical and mean. Roasts habits, never looks, and safety lines stay plain.
STYLES["mean"] = {**STYLES["deadpan"], **{
    # mornings
    "alarm": ["It's {time}. Get up. The day isn't going to waste itself. That's your job.",
              "{time}. Rise and shine. Mostly rise. Shine has never really been on the table.",
              "Good morning. It's {time}, and your bed has filed a complaint about the extended stay."],
    "wake_still_in_bed": ["Turning me off while lying down isn't getting up. It's just lying down, louder. "
                          "Back in {minutes} minutes.",
                          "Cute. You silenced me without moving a single muscle you'd need to stand. "
                          "Round two in {minutes} minutes."],
    "wake_nag": ["This is your {count} reminder. At this point the bed is paying more rent than you.",
                 "Still horizontal. Truly, an inspiring commitment to nothing.",
                 "The bed sensor says you're still there. The bed sensor is embarrassed for you.",
                 "Your {count} reminder. I'm a house. I have all day. You don't."],
    "wake_snooze_denied": ["No. You've snoozed {snoozes} times. That's not resting, that's a lifestyle.",
                           "Snooze denied. I've reviewed your decision-making and I'm overruling it.",
                           "No more snoozes. The snooze button has been retired for its own safety."],
    "wake_up": ["Oh look, you're vertical. That only took {minutes} minutes. Someone get a medal. "
                "A small one.",
                "You're up. {minutes} minutes, {snoozes} snoozes. I'll add it to your permanent record.",
                "Upright at last. Evolution took millions of years. You took {minutes} minutes. Close race."],
    "wake_back_to_bed": ["Back in bed? Bold. Stupid, but bold. Alarm's back on.",
                         "I saw that. The bed saw that. We're both disappointed. Get up."],
    "wake_gave_up": ["Fine. Stay there. I've told your phone you're a lost cause.",
                     "I give up. Which, for the record, is the first thing either of us has done today."],
    "snoozed": ["Fine. {minutes} more minutes of being unconscious.",
                "Snoozing {minutes} minutes. The clock keeps going. It doesn't care about you either."],
    "wake_snoozed": ["Fine. {minutes} more minutes of being unconscious. {left} snoozes left. Spend them wisely. "
                     "You won't.", "Snoozing {minutes} minutes. {left} left. The clock doesn't care about you either."],
    "alarm_set": "Alarm set for {time}. We both know you'll ignore it. I'm setting it anyway. That's called hope.",
    "good_morning": ["Good morning. Another day for you to almost do things.",
                     "Morning. You're awake, technically. Let's not oversell it."],
    # doors
    "retinal_scan_complete": ["Retinal scan complete. Oh. It's you, {name}. I'd hoped for someone more interesting.",
                              "Retinal scan complete. Welcome back, {name}. The house was quieter without you. "
                              "Better, too."],
    "retinal_scan_failed": ["Retinal scan failed. I don't know you, and based on this, I don't want to. "
                            "Locking the {door} door.",
                            "Retinal scan failed. Whoever you are, the {door} door has decided against you."],
    "fingerprint_prompt": "Finger on the scanner. Try to use one of your own.",
    "fingerprint_accepted": "Fingerprint accepted. Welcome home, {name}. Lower your expectations accordingly.",
    "fingerprint_rejected": "I don't recognize that fingerprint. I barely recognize that finger. Again.",
    "fingerprint_lockout": "Too many failures. The {door} door is locked. It's your own finger. "
                           "How did you get this wrong?",
    "access_denied": "Access denied. Don't take it personally. Actually, do.",
    "door_locked": "The {door} door is locked. Someone around here has to be responsible.",
    "door_auto_locked": "Somebody left the {door} door unlocked again. I won't say who. It was you.",
    "door_left_open": "The {door} door has been open for ages. Are we heating the whole street now? How generous.",
    "doorbell_announce": "Someone's at the {door} door. Go on, pretend you're not home. You're good at that.",
    "welcome_person": ["Hello, {name}. Didn't hear you come in. Didn't miss you either.",
                       "{name}. You're back. Lovely. For you."],
    "welcome_home": ["Oh, you're back. I'd just started enjoying the silence.",
                     "Welcome home. Shoes off. You live here, you don't own the place. I do."],
    # routines
    "lockdown": "Lockdown engaged. Doors locked, lights off. The house is finally the way I like it: "
                "unbothered.",
    "goodnight": "Good night. I'll stay up all night keeping you safe. Don't thank me. You never do.",
    "leaving": "Leaving? I'll lock up. Try to accomplish something out there. Anything. I'll wait.",
    # timers and reminders
    "timer_set": "Timer set for {duration}. Don't wander off and forget, like you do.",
    "timer_done": ["Timer's done. Whatever's in there, I'm sure it's fine. It's not fine.",
                   "Your timer's done. Go deal with whatever you started and then abandoned."],
    "timer_done_named": "Your {label} timer is done. Try to look like you meant to do that.",
    "timer_cancelled": "Timer cancelled. Another plan, abandoned. Very on brand.",
    "no_timers": "There are no timers. Nothing's cooking. Shocking, I know.",
    "reminder": ["Reminder: {text}. You asked me to remember because you can't.",
                 "{name}. Reminder: {text}. Yes, again. Yes, it's still your problem."],
    # the house
    "privacy_on": "Fine. I've stopped listening. Whatever you're about to say, I'm sure it's riveting.",
    "privacy_off": "I'm listening again. Try to say something worth hearing this time.",
    "dnd_on": "Do not disturb is on. I'll keep my opinions to myself. This is going to be hard.",
    "dnd_off": "Do not disturb is off. I've been saving up.",
    "mode_changed": "The house is in {mode} mode now. Hold your applause.",
    "time_now": "It's {time}. Later than you think. It always is with you.",
    "date_now": "It's {date}. Another one.",
    "listening": ["What.", "Yes? Make it quick.", "I'm listening. Unfortunately."],
    "assistant_error": "I can't answer that right now. Enjoy not knowing. You're used to it.",
    "device_offline": "The {node} module went offline. Can't say I blame it.",
    "alarm_arming": "The alarm turns on in {seconds} seconds. Leave, or don't. I'm watching either way.",
    "alarm_alert": "The alarm wasn't disarmed. The owners have been told. The cameras are recording. Smile.",
    "alarm_disarmed": "Alarm off. Welcome home. Took you long enough.",
    "package_delivered": "A package arrived: {description}. More stuff. Exactly what this house was missing.",
    "package_taken": "The package on the porch is gone and nobody here took it. So that's gone. Great job, everyone.",
    "mail": "You've got mail. Bills, probably. It's always bills.",
    "all_clear": "All clear. Nothing happened. Like every other day around here.",
}}

# `voice: {effect: synthetic}` (or any ffmpeg -af filter string). {up}/{rate} are filled in.
EFFECTS: dict[str, str] = {
    # a cool, slightly-too-perfect machine voice: nudged up in pitch, metallic, a hint of echo
    "synthetic": ("asetrate={up},aresample={rate},atempo={back},"
                  "flanger=delay=0:depth=1.5:regen=35:speed=0.25,"
                  "aecho=0.85:0.55:9:0.2,highpass=f=140,lowpass=f=8500,volume=1.4"),
    "robot": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75,volume=1.2",
    "deep": "asetrate={down},aresample={rate},atempo={forward}",
    "radio": "highpass=f=300,lowpass=f=3400,acompressor=threshold=0.1:ratio=6,volume=1.5",
}


def effect_filter(effect: str, rate: int) -> str:
    chain = EFFECTS.get(effect, effect)
    return chain.format(rate=rate, up=int(rate * 1.06), back=f"{1 / 1.06:.4f}",
                        down=int(rate * 0.88), forward=f"{1 / 0.88:.4f}")


# `phrasehome voice demo`: a long sample of what the house says, with example values filled in
DEMO: list[tuple[str, dict]] = [
    ("alarm", {"time": "6:45 AM"}),
    ("wake_still_in_bed", {"minutes": "3"}),
    ("wake_nag", {"count": "second"}),
    ("wake_snoozed", {"minutes": "9", "left": "1"}),
    ("wake_snooze_denied", {"snoozes": "2"}),
    ("wake_back_to_bed", {}),
    ("wake_up", {"minutes": "14", "snoozes": "2", "nags": "3"}),
    ("good_morning", {}),
    ("retinal_scan_complete", {"name": "Finn"}),
    ("retinal_scan_failed", {"door": "front"}),
    ("fingerprint_prompt", {}),
    ("fingerprint_rejected", {}),
    ("door_auto_locked", {"door": "front"}),
    ("door_left_open", {"door": "back"}),
    ("doorbell_announce", {"door": "front"}),
    ("welcome_home", {}),
    ("leaving", {}),
    ("timer_set", {"duration": "10 minutes"}),
    ("timer_done", {}),
    ("timer_done_named", {"label": "pizza"}),
    ("reminder", {"name": "Finn", "text": "take the trash out"}),
    ("listening", {}),
    ("privacy_on", {}),
    ("package_delivered", {"description": "a box from the hardware store"}),
    ("package_taken", {}),
    ("mail", {}),
    ("device_offline", {"node": "garage"}),
    ("lockdown", {}),
    ("goodnight", {}),
    ("smoke_alarm", {"room": "kitchen"}),  # safety lines stay plain in every style
]


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class Voice:
    def __init__(self, config, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        cfg = config.voice
        self.base = {
            "engine": cfg.get("engine", "piper"),
            "piper_model": cfg.get("piper_model", "models/piper/en_US-lessac-medium.onnx"),
            "espeak_voice": cfg.get("espeak_voice", "en-us"),
            "speed": float(cfg.get("speed", 1.0)),
            "command": cfg.get("command"),
            "volume": float(cfg.get("volume", 100)),
            "effect": cfg.get("effect"),
        }
        self.quiet_volume = float(cfg.get("quiet_volume", 40))
        self.bank_dir = Path(config.resolve_path(cfg.get("bank_dir", "voice_bank")))
        self.cache = Path(config.resolve_path(config.data_dir)) / "voice_cache"
        style = cfg.get("style", DEFAULT_STYLE)  # `style: plain` for the polite, straight lines
        if style == "plain":
            style = None
        if style and style not in STYLES:
            log.warning("unknown voice style %r (have: %s)", style, ", ".join(STYLES))
        self.phrases = {**DEFAULT_PHRASES, **STYLES.get(style or "", {}), **(cfg.get("phrases") or {})}
        self._lock = threading.Lock()
        self._router = None

    @property
    def router(self):
        if self._router is None:
            from .audio_out import AudioRouter

            self._router = AudioRouter(self.config.home)
        return self._router

    # ---------- choosing what to say ----------
    def resolve(self, key_or_text: str, lang: str | None = None, **values: Any) -> tuple[str, Path | None]:
        """Returns (text, recorded clip or built-in sound or None). Unknown keys are spoken as text."""
        key = key_or_text.strip()
        clip = self._clip(key)
        entry = self.phrases.get(key)
        if isinstance(entry, dict) and not ({"text", "file"} & set(entry)):  # {en: ..., es: ...}
            entry = entry.get(lang or "", entry.get(self.config.settings.language, next(iter(entry.values()))))
        if entry is None and clip is None:
            from .sounds import make

            sound = make(key, self.cache)
            if sound:
                return "", sound
            entry = key
        if entry is None:
            entry = ""
        if isinstance(entry, list):
            entry = random.choice(entry)
        if isinstance(entry, dict):  # {text: ..., file: ...}
            if entry.get("file"):
                clip = Path(self.config.resolve_path(entry["file"]))
            entry = entry.get("text", key)
        text = str(entry).format_map(_Safe({k: v for k, v in values.items() if v is not None}))
        return text, clip

    def _clip(self, key: str) -> Path | None:
        if not key.replace("_", "").isalnum():
            return None
        folder = self.bank_dir / key
        if folder.is_dir():
            clips = sorted(folder.glob("*.wav"))
            if clips:
                return random.choice(clips)
        f = self.bank_dir / f"{key}.wav"
        return f if f.exists() else None

    def settings_for(self, room: str | None) -> dict[str, Any]:
        """The base voice, with the room's instance overrides (its own voice, speed, volume)."""
        v = dict(self.base)
        home = getattr(self.config, "home", None)
        if home is not None and getattr(home, "instances", None):
            v.update(home.instances.for_room(room).voice)
        return v

    # ---------- making sound ----------
    @staticmethod
    def available_engine(engine: str) -> str:
        """Piper isn't installed (e.g. on a Mac while testing)? Use what this computer has."""
        if engine != "piper" or shutil.which("piper"):
            return engine
        if sys.platform == "darwin" and shutil.which("say"):
            return "say"  # macOS's built-in voice
        if shutil.which("espeak-ng"):
            return "espeak"
        return engine

    def synthesize(self, text: str, v: dict[str, Any] | None = None) -> Path | None:
        v = v or self.base
        engine = self.available_engine(v.get("engine", "piper"))
        if engine == "none" or not text.strip():
            return None
        ident = f"{engine}|{v.get('say_voice')}|{v.get('piper_model')}|{v.get('espeak_voice')}|{v.get('speed')}|{v.get('command')}|{v.get('effect')}|{text}"
        wav = self.cache / (hashlib.sha1(ident.encode()).hexdigest() + ".wav")
        if wav.exists():
            return wav
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = wav.with_name(f"{wav.stem}.{uuid.uuid4().hex[:8]}.tmp.wav")  # never shared between threads
        speed = float(v.get("speed", 1.0))
        if engine == "piper":
            cmd = ["piper", "--model", str(self.config.resolve_path(v["piper_model"])),
                   "--length_scale", str(1 / speed), "--output_file", str(tmp)]
            subprocess.run(cmd, input=text.encode(), check=True, capture_output=True, timeout=60)
        elif engine == "say":  # macOS
            cmd = ["say", "--file-format=WAVE", "--data-format=LEI16@22050",
                   "-r", str(int(180 * speed)), "-o", str(tmp), text]
            if v.get("say_voice"):
                cmd[1:1] = ["-v", str(v["say_voice"])]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        elif engine == "espeak":
            cmd = ["espeak-ng", "-v", str(v.get("espeak_voice", "en-us")), "-s", str(int(170 * speed)),
                   "-w", str(tmp), text]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        elif engine == "command":
            command = v.get("command")
            if not isinstance(command, list):
                raise ValueError("voice.command must be a list like [my-tts, --out, '{wav}', '{text}']")
            cmd = [str(c).replace("{text}", text).replace("{wav}", str(tmp)) for c in command]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        else:
            raise ValueError(f"unknown voice engine '{engine}' (piper, espeak, say, command, none)")
        if v.get("effect"):
            self._apply_effect(tmp, str(v["effect"]))
        os.replace(tmp, wav)
        return wav

    @staticmethod
    def _apply_effect(wav: Path, effect: str) -> None:
        if not shutil.which("ffmpeg"):
            log.warning("voice effect %r needs ffmpeg; speaking without it", effect)
            return
        import wave

        with wave.open(str(wav)) as w:
            rate = w.getframerate()
        out = wav.with_name(wav.stem + ".fx.wav")
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-af",
                            effect_filter(effect, rate), str(out)], capture_output=True, timeout=60)
        if r.returncode == 0 and out.exists():
            os.replace(out, wav)
        else:
            out.unlink(missing_ok=True)
            log.warning("voice effect %r failed: %s", effect, r.stderr.decode(errors="replace")[-200:])

    def targets(self, room: str | None, rooms: list[str] | None, everywhere: bool,
                urgent: bool) -> list[str | None]:
        home = getattr(self.config, "home", None)
        if everywhere and home is not None:
            rooms = sorted(self.router.rooms()) or [None]
        if not rooms:
            return [room]
        out = []
        for r in rooms:
            r = str(r).lower() if r else None
            if home is not None and not urgent and r:
                inst = home.instances.for_room(r)
                if home.instances.dnd(inst) or inst.quiet_now():
                    continue  # broadcasts skip rooms in do-not-disturb or quiet hours
            out.append(r)
        return out

    def speak(self, key_or_text: str, room: str | None = None, rooms: list[str] | None = None,
              everywhere: bool = False, urgent: bool = False, **values: Any) -> str:
        home = getattr(self.config, "home", None)
        lang = home.language_for(room) if home is not None and hasattr(home, "language_for") else None
        text, clip = self.resolve(key_or_text, lang=lang, **values)
        targets = self.targets(room, rooms, everywhere, urgent)
        if self.dry_run or not targets:
            log.info("%ssay%s: %s", "DRY RUN " if self.dry_run else "",
                     f" [{', '.join(t or 'hub' for t in targets)}]" if targets else " (nobody to tell)",
                     text or clip)
            return text
        with self._lock:
            # make the audio once per voice (most rooms share one), then play everywhere at once
            made: dict[str, Path | None] = {}
            plays = []
            home = getattr(self.config, "home", None)
            for t in targets:
                v = self.settings_for(t)
                key = repr(sorted((k, str(x)) for k, x in v.items() if k != "volume"))
                if key not in made:
                    try:
                        made[key] = clip or self.synthesize(text, v)
                    except Exception as e:
                        log.error("couldn't make speech for %r: %s", text, e)
                        made[key] = None
                if not made[key]:
                    continue
                volume = float(v.get("volume", 100))
                if home is not None and t and home.instances.for_room(t).quiet_now():
                    volume = min(volume, self.quiet_volume)
                plays.append((t, made[key], volume))
            # rooms with no speaker of their own fall back to the hub's speaker: play that only once
            threads, local_done = [], False
            for room, audio, volume in plays:
                route = self.router.route(room)
                if route == "local":
                    if local_done:
                        continue
                    local_done = True
                if route == "none":
                    log.info("no speaker in %s; not played", room)
                    continue
                th = threading.Thread(target=self._play_one, args=(text, audio, room, volume, route), daemon=True)
                th.start()
                threads.append(th)
            for th in threads:
                th.join(timeout=120)
        return text

    def _play_one(self, text: str, audio: Path, room: str | None, volume: float, route: str) -> None:
        try:
            where = self.router.play(audio, room, volume / 100, route=route)
            log.info("say [%s via %s]: %s", room or "hub", where, text or audio.name)
        except Exception as e:
            log.error("couldn't speak %r in %s: %s", text, room or "hub", e)

    def demo_lines(self, every_variant: bool = True) -> list[tuple[str, str]]:
        """(key, text) for the demo: each sample line, every variant of it."""
        out = []
        for key, values in DEMO:
            entry = self.phrases.get(key)
            if entry is None:
                continue
            if isinstance(entry, dict) and not ({"text", "file"} & set(entry)):
                entry = next(iter(entry.values()))
            variants = entry if isinstance(entry, list) else [entry]
            for e in variants if every_variant else variants[:1]:
                text = e.get("text", key) if isinstance(e, dict) else str(e)
                out.append((key, text.format_map(_Safe(values))))
        return out

    def demo(self, save: Path | None = None, every_variant: bool = True, gap: float = 0.7,
             show=print) -> Path | None:
        """Say the demo lines one after another, or write them all to one audio file."""
        from .audio_out import convert

        lines = self.demo_lines(every_variant)
        if save is None:
            for key, text in lines:
                show(f"[{key}] {text}")
                self.speak(text)
            return None
        rate, pcm = 22050, bytearray()
        silence = b"\x00\x00" * int(rate * gap)
        for key, text in lines:
            show(f"[{key}] {text}")
            wav = self.synthesize(text, self.base)
            if wav:
                pcm += convert(wav, rate, 1) + silence
        save = Path(save)
        save.parent.mkdir(parents=True, exist_ok=True)
        import wave

        tmp = save.with_suffix(".tmp.wav")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(pcm))
        if save.suffix.lower() == ".wav":
            os.replace(tmp, save)
        else:  # .mp3, .m4a...
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(tmp), str(save)],
                           check=True, capture_output=True, timeout=120)
            tmp.unlink(missing_ok=True)
        return save

    def record(self, key: str, seconds: float = 4.0) -> Path:
        self.bank_dir.mkdir(parents=True, exist_ok=True)
        out = self.bank_dir / f"{key}.wav"
        if out.exists():
            out.rename(out.with_suffix(".wav.bak"))
        subprocess.run(["arecord", "-q", "-f", "cd", "-c", "1", "-d", str(int(seconds)), str(out)],
                       check=True)
        return out
