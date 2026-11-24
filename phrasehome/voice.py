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

Speech plays on the hub's own speaker and, optionally, through the ESP32 speaker group.
"""

from __future__ import annotations

import hashlib
import logging
import random
import subprocess
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
        }
        self.quiet_volume = float(cfg.get("quiet_volume", 40))
        self.bank_dir = Path(config.resolve_path(cfg.get("bank_dir", "voice_bank")))
        self.cache = Path(config.resolve_path(config.data_dir)) / "voice_cache"
        self.phrases = {**DEFAULT_PHRASES, **(cfg.get("phrases") or {})}
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
    def synthesize(self, text: str, v: dict[str, Any] | None = None) -> Path | None:
        v = v or self.base
        engine = v.get("engine", "piper")
        if engine == "none" or not text.strip():
            return None
        ident = f"{engine}|{v.get('piper_model')}|{v.get('espeak_voice')}|{v.get('speed')}|{v.get('command')}|{text}"
        wav = self.cache / (hashlib.sha1(ident.encode()).hexdigest() + ".wav")
        if wav.exists():
            return wav
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = wav.with_suffix(".tmp.wav")
        speed = float(v.get("speed", 1.0))
        if engine == "piper":
            cmd = ["piper", "--model", str(self.config.resolve_path(v["piper_model"])),
                   "--length_scale", str(1 / speed), "--output_file", str(tmp)]
            subprocess.run(cmd, input=text.encode(), check=True, capture_output=True, timeout=60)
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
            raise ValueError(f"unknown voice engine '{engine}' (piper, espeak, command, none)")
        tmp.rename(wav)
        return wav

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
            threads = []
            for t in targets:
                th = threading.Thread(target=self._speak_one, args=(text, clip, t), daemon=True)
                th.start()
                threads.append(th)
            for th in threads:
                th.join(timeout=120)
        return text

    def _speak_one(self, text: str, clip: Path | None, room: str | None) -> None:
        try:
            v = self.settings_for(room)
            audio = clip or self.synthesize(text, v)
            if not audio:
                return
            volume = float(v.get("volume", 100))
            home = getattr(self.config, "home", None)
            if home is not None and room and home.instances.for_room(room).quiet_now():
                volume = min(volume, self.quiet_volume)
            where = self.router.play(audio, room, volume / 100)
            log.info("say [%s via %s]: %s", room or "hub", where, text or audio.name)
        except Exception as e:
            log.error("couldn't speak %r in %s: %s", text, room or "hub", e)

    def record(self, key: str, seconds: float = 4.0) -> Path:
        self.bank_dir.mkdir(parents=True, exist_ok=True)
        out = self.bank_dir / f"{key}.wav"
        if out.exists():
            out.rename(out.with_suffix(".wav.bak"))
        subprocess.run(["arecord", "-q", "-f", "cd", "-c", "1", "-d", str(int(seconds)), str(out)],
                       check=True)
        return out
