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
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.voice")

DEFAULT_PHRASES: dict[str, Any] = {
    "retinal_scan_complete": ["Retinal scan complete. Welcome home, {name}."],
    "retinal_scan_failed": ["Retinal scan failed. Locking the {door} door."],
    "fingerprint_prompt": "Please place your finger on the scanner.",
    "access_denied": "Access denied.",
    "door_locked": "The {door} door is locked.",
    "lockdown": "Lockdown engaged. All doors are locked and the lights are off.",
    "goodnight": "Good night. The house is secure.",
    "leaving": "Goodbye. I'll lock up behind you.",
    "welcome_home": "Welcome home.",
    "panic": "Emergency mode. All lights on.",
    "listening": ["Yes?", "I'm listening."],
    "assistant_error": "Sorry, I can't answer that right now.",
}


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class Voice:
    def __init__(self, config, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        cfg = config.voice
        self.engine = cfg.get("engine", "piper")
        self.model = cfg.get("piper_model", "models/piper/en_US-lessac-medium.onnx")
        self.espeak_voice = cfg.get("espeak_voice", "en-us")
        self.speed = float(cfg.get("speed", 1.0))
        self.command = cfg.get("command")  # custom engine: list with {text} and {wav}
        self.player = cfg.get("player", ["aplay", "-q", "{file}"])
        self.play_local = cfg.get("local", True)
        self.speakers_backend = cfg.get("speakers")  # name of a `type: speakers` backend
        self.bank_dir = Path(config.resolve_path(cfg.get("bank_dir", "voice_bank")))
        self.cache = Path(config.resolve_path(config.data_dir)) / "voice_cache"
        self.phrases = {**DEFAULT_PHRASES, **(cfg.get("phrases") or {})}
        self._lock = threading.Lock()

    # ---------- choosing what to say ----------
    def resolve(self, key_or_text: str, **values: Any) -> tuple[str, Path | None]:
        """Returns (text, recorded clip or None). Unknown keys are spoken as literal text."""
        key = key_or_text.strip()
        clip = self._clip(key)
        entry = self.phrases.get(key, key)
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

    # ---------- making sound ----------
    def synthesize(self, text: str) -> Path | None:
        if self.engine == "none":
            return None
        ident = f"{self.engine}|{self.model}|{self.espeak_voice}|{self.speed}|{self.command}|{text}"
        wav = self.cache / (hashlib.sha1(ident.encode()).hexdigest() + ".wav")
        if wav.exists():
            return wav
        self.cache.mkdir(parents=True, exist_ok=True)
        tmp = wav.with_suffix(".tmp.wav")
        if self.engine == "piper":
            cmd = ["piper", "--model", str(self.config.resolve_path(self.model)),
                   "--length_scale", str(1 / self.speed), "--output_file", str(tmp)]
            subprocess.run(cmd, input=text.encode(), check=True, capture_output=True, timeout=60)
        elif self.engine == "espeak":
            cmd = ["espeak-ng", "-v", self.espeak_voice, "-s", str(int(170 * self.speed)),
                   "-w", str(tmp), text]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        elif self.engine == "command":
            if not isinstance(self.command, list):
                raise ValueError("voice.command must be a list like [my-tts, --out, '{wav}', '{text}']")
            cmd = [str(c).replace("{text}", text).replace("{wav}", str(tmp)) for c in self.command]
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        else:
            raise ValueError(f"unknown voice engine '{self.engine}' (piper, espeak, command, none)")
        tmp.rename(wav)
        return wav

    def play(self, audio: Path) -> None:
        if self.play_local and self.player:
            cmd = [str(c).replace("{file}", str(audio)) for c in self.player]
            subprocess.run(cmd, capture_output=True, timeout=120)
        if self.speakers_backend:
            self._to_speakers(audio)

    def _to_speakers(self, audio: Path) -> None:
        if not shutil.which("ffmpeg"):
            log.warning("ffmpeg not installed: can't send speech to the speaker group")
            return
        pcm = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(audio), "-f", "s16le", "-ar", "44100",
             "-ac", "2", "-"], capture_output=True, timeout=60).stdout
        self.config.home.backend(self.speakers_backend).hub.announce(pcm)

    def speak(self, key_or_text: str, **values: Any) -> str:
        text, clip = self.resolve(key_or_text, **values)
        if self.dry_run:
            log.info("DRY RUN say: %s", text)
            return text
        with self._lock:  # one thing at a time
            try:
                audio = clip or self.synthesize(text)
                log.info("say: %s%s", text, f"  [{clip.name}]" if clip else "")
                if audio:
                    self.play(audio)
            except Exception as e:
                log.error("couldn't speak %r: %s", text, e)
        return text

    def record(self, key: str, seconds: float = 4.0) -> Path:
        self.bank_dir.mkdir(parents=True, exist_ok=True)
        out = self.bank_dir / f"{key}.wav"
        if out.exists():
            out.rename(out.with_suffix(".wav.bak"))
        subprocess.run(["arecord", "-q", "-f", "cd", "-c", "1", "-d", str(int(seconds)), str(out)],
                       check=True)
        return out
