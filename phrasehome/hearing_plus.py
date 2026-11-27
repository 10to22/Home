"""Better hearing: learned accent fixes, a noise profile per room, echo cancelling, mic health, the
direction a voice came from, and not taking orders from the TV.

    hearing:
      accent_fixes: true        # words the house keeps mishearing, learned from the History page
      keep_clips: false         # keep the audio of misheard sentences (data/clips/) for fine-tuning
      room_noise: false         # learn each room's steady background noise and take it out
      echo_cancel: false        # pip install speexdsp: hear you over the house's own speaker
      tv:                       # rooms with a TV: while it's on, commands from voices the house
        living room: { plug: tv }      # doesn't know need the name ("house, ...")
        # or { sensor: [living room, tv] } or { watts: 30 } with the plug
      ignore_directions:        # a mic array that knows where a voice came from (ReSpeaker USB)
        living room: [[60, 120]]       # degrees: the TV's corner; commands from there need the name

Accent fixes. When you teach the house on the History page that "turn on the kitten lights" meant
"turn on the kitchen lights", it notes kitten -> kitchen. After the same fix twice, it applies it to
everything it hears. `phrasehome accent` lists them; `phrasehome accent export` writes the kept clips
and what they meant as a dataset for fine-tuning Whisper on your family's voices.
"""

from __future__ import annotations

import difflib
import json
import logging
import struct
import threading
import time
import wave
from collections import Counter, deque
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("phrasehome.hearing")
RATE = 16000


def _cfg(config) -> dict:
    return dict(config.extra.get("hearing") or {})


# ---------- accent fixes ----------
class AccentFixes:
    """Word swaps learned from what people taught the house (data/accent.json)."""

    MIN_SEEN = 2

    def __init__(self, config) -> None:
        self.config = config
        self.enabled = bool(_cfg(config).get("accent_fixes", True))
        self.path = Path(config.resolve_path(config.data_dir)) / "accent.json"
        self._cache: dict[str, int] | None = None

    def counts(self) -> dict[str, int]:
        if self._cache is None:
            try:
                self._cache = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}
        return self._cache

    @staticmethod
    def differences(heard: str, meant: str) -> list[tuple[str, str]]:
        """Single-word swaps between what was heard and what was meant ("kitten" -> "kitchen")."""
        from .matcher import normalize

        a, b = normalize(heard).split(), normalize(meant).split()
        out = []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
            if op == "replace" and i2 - i1 == j2 - j1:
                out += [(x, y) for x, y in zip(a[i1:i2], b[j1:j2]) if x != y and x != "unk"]
        return out

    def learn(self, heard: str, meant: str) -> list[tuple[str, str]]:
        swaps = self.differences(heard, meant)
        if not swaps or not self.enabled:
            return []
        counts = self.counts()
        for x, y in swaps:
            counts[f"{x}>{y}"] = counts.get(f"{x}>{y}", 0) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(counts, indent=1), encoding="utf-8")
        return swaps

    def rules(self) -> dict[str, str]:
        best: dict[str, tuple[int, str]] = {}
        try:  # fixes learned before shipping (train-hearing), unless this house taught something else
            from .hearing_train import shipped

            fixes = shipped().get("word_fixes", {})
            if fixes:
                if getattr(self, "_vocab", None) is None:
                    from .matcher import expand

                    self._vocab = {w for c in expand(self.config.commands, self.config.slots) for w in c.words}
                for x, y in fixes.items():
                    if x not in self._vocab:  # never rewrite a word this house's own commands use
                        best[x] = (self.MIN_SEEN, y)
        except Exception:
            pass
        for key, n in self.counts().items():
            x, _, y = key.partition(">")
            if n >= self.MIN_SEEN and n > best.get(x, (0, ""))[0]:
                best[x] = (n, y)
        return {x: y for x, (n, y) in best.items()}

    def apply(self, text: str) -> str:
        if not self.enabled:
            return text
        rules = self.rules()
        if not rules:
            return text
        from .matcher import normalize

        words = normalize(text).split()
        fixed = [rules.get(w, w) for w in words]
        return " ".join(fixed) if fixed != words else text


def keep_clip(config, entry_id: str, pcm: bytes) -> Path | None:
    """Save the audio of a misheard sentence (hearing: {keep_clips: true}), named by its History id."""
    if not pcm or not _cfg(config).get("keep_clips"):
        return None
    d = Path(config.resolve_path(config.data_dir)) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{entry_id}.wav"
    with wave.open(str(f), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return f


def export_dataset(config, history_entries: list[dict], out: Path | None = None) -> tuple[Path, int]:
    """Kept clips + what they really meant (taught on the History page) as a fine-tuning dataset:
    a folder of WAVs and metadata.jsonl ({"audio": "x.wav", "text": "..."}), the usual layout for
    Whisper fine-tuning scripts (Hugging Face `datasets` reads it as an audiofolder)."""
    from .matcher import normalize
    from .understanding import Aliases, meant_text

    clips = Path(config.resolve_path(config.data_dir)) / "clips"
    out = out or Path(config.resolve_path(config.data_dir)) / "accent-dataset"
    out.mkdir(parents=True, exist_ok=True)
    taught = {a["heard"]: a.get("meant") or meant_text(config, a["command"], a.get("slots"))
              for a in Aliases(config).all()}
    n = 0
    with (out / "metadata.jsonl").open("w", encoding="utf-8") as meta:
        for e in history_entries:
            text = taught.get(normalize(e.get("heard") or ""))
            f = clips / f"{e.get('id')}.wav"
            if text and f.exists():
                (out / f.name).write_bytes(f.read_bytes())
                meta.write(json.dumps({"file_name": f.name, "text": text}) + "\n")
                n += 1
    return out, n


# ---------- a noise profile per room ----------
class RoomNoise:
    """Learns the steady background noise of a room (fridge, fan, aircon) during quiet moments and
    takes it out (spectral subtraction with overlap-add). Saved per room in data/noise/."""

    N = 512
    HOP = 256

    def __init__(self, config, room: str | None) -> None:
        self.path = Path(config.resolve_path(config.data_dir)) / "noise" / f"{(room or 'hub').replace(' ', '_')}.npy"
        self.win = np.sqrt(np.hanning(self.N + 1)[:-1])
        try:
            self.profile = np.load(self.path)
        except (OSError, ValueError):
            self.profile = None
        self.inbuf = np.zeros(self.N - self.HOP)
        self.outbuf = np.zeros(self.N)
        self.quiet_frames = 0
        self._saved = time.monotonic()

    def _learn(self, mag: np.ndarray) -> None:
        self.profile = mag.copy() if self.profile is None else 0.98 * self.profile + 0.02 * mag
        self.quiet_frames += 1
        if time.monotonic() - self._saved > 300:
            self.save()

    def save(self) -> None:
        if self.profile is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            np.save(self.path, self.profile)
            self._saved = time.monotonic()

    def __call__(self, pcm: bytes) -> bytes:
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
        data = np.concatenate([self.inbuf, x])
        out = []
        i = 0
        while i + self.N <= len(data):
            frame = data[i:i + self.N] * self.win
            spec = np.fft.rfft(frame)
            mag = np.abs(spec)
            if float(np.sqrt(np.mean(frame ** 2))) < 0.004:  # quiet: this is the room, not a person
                self._learn(mag)
            if self.profile is not None:
                gain = np.maximum(1 - 1.5 * self.profile / (mag + 1e-9), 0.12)
                spec = spec * gain
            y = np.fft.irfft(spec, self.N) * self.win
            self.outbuf += y
            out.append(self.outbuf[: self.HOP].copy())
            self.outbuf = np.concatenate([self.outbuf[self.HOP:], np.zeros(self.HOP)])
            i += self.HOP
        self.inbuf = data[i:]
        y = np.concatenate(out) if out else np.zeros(0)
        return (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()


# ---------- echo cancelling (the hub's own speaker) ----------
class FarEnd:
    """What the hub's speaker is playing, as 16 kHz audio with the time it started."""

    def __init__(self, keep_seconds: float = 30.0) -> None:
        self.parts: deque[tuple[float, np.ndarray]] = deque()
        self.keep = keep_seconds
        self._lock = threading.Lock()
        self.listening = False  # set when echo cancelling is on, so nothing is converted otherwise

    def playing(self, pcm: bytes, start: float | None = None) -> None:
        with self._lock:
            self.parts.append((start if start is not None else time.monotonic(), np.frombuffer(pcm, dtype=np.int16)))
            newest = max(t0 + len(a) / RATE for t0, a in self.parts)
            cutoff = newest - self.keep
            while self.parts and self.parts[0][0] + len(self.parts[0][1]) / RATE < cutoff:
                self.parts.popleft()

    def window(self, end: float, n: int, delay: float = 0.0) -> np.ndarray:
        """The n samples that were playing up to `end` (seconds, monotonic), minus `delay`."""
        out = np.zeros(n, dtype=np.int16)
        start = end - delay - n / RATE
        with self._lock:
            for t0, a in self.parts:
                i0 = int(round((start - t0) * RATE))
                lo, hi = max(0, i0), min(len(a), i0 + n)
                if lo < hi:
                    out[lo - i0:hi - i0] = a[lo:hi]
        return out


far_end = FarEnd()


class EchoCanceller:
    """Takes the house's own voice and music out of the hub mic (speexdsp), so you can talk over it."""

    FRAME = 256

    def __init__(self, delay: float = 0.05, tail_ms: int = 300, module=None) -> None:
        if module is None:
            try:
                import speexdsp as module
            except ImportError:
                raise RuntimeError("echo cancelling needs speexdsp: .venv/bin/pip install speexdsp") from None
        self.ec = module.EchoCanceller.create(self.FRAME, int(RATE * tail_ms / 1000), RATE)
        self.delay = delay
        self.rest = b""

    def __call__(self, pcm: bytes, now: float | None = None) -> bytes:
        now = time.monotonic() if now is None else now
        data = self.rest + pcm
        n = len(data) // (2 * self.FRAME) * self.FRAME
        if not n:
            self.rest = data
            return b""
        ref = far_end.window(now, len(data) // 2, self.delay)[:n]
        out = bytearray()
        for i in range(0, n, self.FRAME):
            near = data[2 * i:2 * (i + self.FRAME)]
            far = ref[i:i + self.FRAME].tobytes()
            out += self.ec.process(near, far) if far.strip(b"\0") else near
        self.rest = data[2 * n:]
        return bytes(out)


# ---------- mic health ----------
class MicHealth:
    """Per microphone: when it last sent audio, how loud, how often it clips. Warns when one goes quiet
    (dead or muted for 10 minutes), clips a lot (too close or gain too high), or stops sending."""

    def __init__(self, home=None, silent_minutes: float = 10, clip_share: float = 0.05,
                 offline_seconds: float = 120) -> None:
        self.home = home
        self.silent = silent_minutes * 60
        self.clip_share = clip_share
        self.offline = offline_seconds
        self.mics: dict[str, dict[str, Any]] = {}
        self.warned: dict[str, str] = {}

    def feed(self, source: str, room: str | None, pcm: bytes, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        x = np.frombuffer(pcm, dtype=np.int16)
        if not len(x):
            return
        m = self.mics.setdefault(source, {"room": room, "first": now, "loud_at": now, "clips": 0, "samples": 0,
                                         "level": 0.0})
        m["room"], m["last"] = room, now
        peak = int(np.abs(x.astype(np.int32)).max())
        rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
        m["level"] = 0.95 * m["level"] + 0.05 * rms
        if peak > 40:
            m["loud_at"] = now
        m["clips"] += int(np.count_nonzero(np.abs(x.astype(np.int32)) >= 32700))
        m["samples"] += len(x)
        if m["samples"] > RATE * 600:  # judge clipping over the last ~10 minutes
            m["clips"] //= 2
            m["samples"] //= 2

    def problems(self, now: float | None = None) -> dict[str, str]:
        now = time.monotonic() if now is None else now
        out = {}
        for src, m in self.mics.items():
            if now - m.get("last", now) > self.offline:
                out[src] = "stopped sending audio"
            elif now - m["loud_at"] > self.silent and now - m["first"] > self.silent:
                out[src] = "only silence (muted, unplugged or broken?)"
            elif m["samples"] > RATE * 30 and m["clips"] / m["samples"] > self.clip_share:
                out[src] = "clipping (too loud: turn the gain down or move it away)"
        return out

    def check(self) -> dict[str, str]:
        probs = self.problems()
        for src, why in probs.items():
            if self.warned.get(src) != why:
                self.warned[src] = why
                room = self.mics[src].get("room")
                log.warning("mic %s%s: %s", src, f" ({room})" if room else "", why)
                if self.home is not None:
                    self.home.events.log("mic.problem", source=src, room=room, problem=why)
        for src in list(self.warned):
            if src not in probs:
                del self.warned[src]
                if self.home is not None:
                    self.home.events.log("mic.ok", source=src)
        return probs

    def status(self) -> list[dict]:
        now = time.monotonic()
        probs = self.problems(now)
        return [{"source": s, "room": m.get("room"), "seconds_ago": round(now - m.get("last", now)),
                 "level": round(m["level"]), "problem": probs.get(s)} for s, m in sorted(self.mics.items())]


# ---------- where the voice came from ----------
class ReSpeakerDOA:
    """Direction of arrival from a ReSpeaker USB 4-mic array (its DOAANGLE tuning parameter).
    Needs pyusb. Untested without the hardware."""

    VID, PID = 0x2886, 0x0018

    def __init__(self) -> None:
        import usb.core
        import usb.util

        self.usb = usb
        self.dev = usb.core.find(idVendor=self.VID, idProduct=self.PID)
        if self.dev is None:
            raise RuntimeError("no ReSpeaker USB mic array found")

    def angle(self) -> int:
        u = self.usb.util
        cmd = 0x80 | 0 | 0x40  # read, offset 0, int
        raw = self.dev.ctrl_transfer(u.CTRL_IN | u.CTRL_TYPE_VENDOR | u.CTRL_RECIPIENT_DEVICE, 0, cmd, 21, 8, 1000)
        return int(struct.unpack(b"ii", bytes(raw))[0])


class Direction:
    """Remembers which way the voice came from during a sentence, and whether that's an ignored
    direction (the TV's corner)."""

    def __init__(self, config, reader=None) -> None:
        self.zones = {str(r).lower(): [tuple(z) for z in zs] for r, zs in
                      (_cfg(config).get("ignore_directions") or {}).items()}
        self.reader = reader
        self.angles: deque[int] = deque(maxlen=50)
        if self.zones and self.reader is None:
            try:
                self.reader = ReSpeakerDOA()
            except Exception as e:
                log.error("can't tell where voices come from: %s", e)

    def feed(self, pcm: bytes) -> None:
        if self.reader is None:
            return
        x = np.frombuffer(pcm, dtype=np.int16)
        if len(x) and float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) > 500:  # someone's talking
            try:
                self.angles.append(int(self.reader.angle()))
            except Exception as e:
                log.debug("direction: %s", e)

    def from_ignored(self, room: str | None) -> bool:
        zones = self.zones.get((room or "").lower())
        if not zones or not self.angles:
            self.angles.clear()
            return False
        angle = Counter(a // 10 * 10 for a in self.angles).most_common(1)[0][0] + 5
        self.angles.clear()
        return any(lo <= angle <= hi if lo <= hi else (angle >= lo or angle <= hi) for lo, hi in zones)


# ---------- the TV ----------
def tv_on(home, room: str | None) -> bool:
    """hearing: {tv: {room: {plug: tv, watts: 30} | {sensor: [node, input]}}}: is that room's TV on?"""
    tv = (_cfg(home.config).get("tv") or {}).get((room or "").lower())
    if not tv:
        return False
    if tv.get("sensor") and getattr(home, "sensors", None):
        node, inp = tv["sensor"]
        cur = home.sensors.current(node, inp) or {}
        return str(cur.get("state", "")).lower() in ("on", "true", "1", "playing")
    if tv.get("plug") and getattr(home, "energy", None):
        plug = home.energy.plugs.get(str(tv["plug"]).lower())
        return bool(plug and plug.power >= float(tv.get("watts", 30)))
    return False


def needs_name(home, room: str | None, person: str | None, from_ignored_direction: bool = False) -> str | None:
    """Why commands said without the name are ignored right now, or None: the TV is on and the voice
    isn't one the house knows, or it came from the TV's direction."""
    if from_ignored_direction:
        return "it came from the TV's direction"
    if person is None and tv_on(home, room):
        return "the TV is on and it wasn't a voice the house knows"
    return None


# ---------- what the listener calls ----------
class HearingPlus:
    """The listener's hooks for everything above, one per audio source where it matters."""

    def __init__(self, engine) -> None:
        self.engine = engine
        config = engine.config
        self.config = config
        cfg = _cfg(config)
        home = getattr(engine, "dispatcher", None)
        home = home if hasattr(home, "config") else None  # (tests use a bare stand-in)
        self.home = home
        self.fixes = AccentFixes(config)
        self.health = MicHealth(home)
        if home is not None:
            home.__dict__["mic_health"] = self.health
        self.room_noise = bool(cfg.get("room_noise"))
        self.noise: dict[str, RoomNoise] = {}
        self.echo = None
        if cfg.get("echo_cancel"):
            try:
                self.echo = EchoCanceller(float(cfg.get("echo_delay", 0.05)))
                far_end.listening = True
                log.info("echo cancelling on: you can talk over the house")
            except Exception as e:
                log.error("echo cancelling off: %s", e)
        self.directions: dict[str, Direction] = {}
        self._dir_cfg = bool(cfg.get("ignore_directions"))

    def echo_on(self, source: str) -> bool:
        return self.echo is not None and source == "local"

    def pre(self, source: str, room: str | None, pcm: bytes) -> bytes:
        self.health.feed(source, room, pcm)
        if self.echo_on(source):
            pcm = self.echo(pcm)
        if self.room_noise and pcm:
            pcm = self.noise.setdefault(source, RoomNoise(self.config, room))(pcm)
        if self._dir_cfg and source == "local" and pcm:
            self.directions.setdefault(source, Direction(self.config)).feed(pcm)
        return pcm

    def fix_text(self, text: str) -> str:
        fixed = self.fixes.apply(text) if text else text
        if fixed != text:
            log.info("heard %r, fixed to %r (learned)", text, fixed)
        return fixed

    def name_needed(self, source: str, room: str | None, person: str | None) -> str | None:
        d = self.directions.get(source)
        return needs_name(self.home, room, person, bool(d and d.from_ignored(room))) if self.home else None

    def after(self, audio: bytes) -> None:
        h = getattr(self.home, "history", None)
        e = getattr(h, "last", None)
        if e and e.get("kind") in ("none", "unclear", "ignored", "asked") and not e.get("_clip"):
            e["_clip"] = True
            keep_clip(self.config, e["id"], audio)
