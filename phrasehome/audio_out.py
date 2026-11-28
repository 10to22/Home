"""Where speech is played, per room:

1. a mic satellite in that room with a speaker attached (the room module)
2. an ESP32 speaker module named after the room, in group mode
3. the hub's own speaker (the room the hub is in, or as a fallback)
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger("phrasehome.audio")

# While the hub's own speaker is talking (and a moment after), its own microphone ignores what it
# hears, so a laptop doesn't hear itself and react to its own voice.
_busy = 0
_quiet_until = 0.0
_busy_lock = threading.Lock()


_procs: set = set()  # players running right now (killed by "shut up")
speaking_text = ""  # what the hub's speaker is saying, to tell "shut up" from its own echo
# After the player stops, the sound is still on its way (Bluetooth / AirPlay speakers lag behind) and the room
# echoes: the mic stays deaf to ordinary speech this long. local_mic: {echo_tail: 2} for AirPlay; Bluetooth
# speakers and AirPods are spotted by name and get 1.5 s (listener.py).
ECHO_TAIL = 0.8
_busy_since = 0.0
_busy_text = ""
_local_spoken: deque = deque(maxlen=40)  # (start, end, text): the hub's own speaker, for muting by time
_spoken: deque = deque(maxlen=80)        # (start, end, text): anything said anywhere, to spot its own words


def local_busy() -> bool:
    return _busy > 0 or time.monotonic() < _quiet_until


def busy_at(t: float | None) -> bool:
    """Was the hub's speaker talking (or still echoing) when audio recorded at monotonic time `t` was heard?
    Audio is often handled a moment after it's recorded (the computer's busy), so this is what to ask, not
    local_busy()."""
    if t is None:
        return local_busy()
    with _busy_lock:
        if _busy > 0 and t >= _busy_since - 0.05:
            return True
        return any(s - 0.05 <= t <= e + ECHO_TAIL for s, e, _ in _local_spoken)


def note_said(text: str, seconds: float, start: float | None = None) -> None:
    """Something was said on a speaker other than the hub's own (a room speaker): remember its words."""
    if text:
        s = time.monotonic() if start is None else start
        with _busy_lock:
            _spoken.append((s, s + max(0.5, seconds), text))


def own_echo(heard: str, at: float | None = None, window: float = 4.0) -> bool:
    """Is `heard` the house's own voice coming back (from any speaker), not a person? Three or more words that
    run along something it said, heard while it spoke or up to `window` seconds after. Shorter things are
    left to the timing (busy_at): "the front" right after "which door, the front or the back?" is an answer."""
    import re

    words = re.findall(r"[a-z0-9']+", heard.lower())
    if len(words) < 3:
        return False
    t = time.monotonic() if at is None else at
    with _busy_lock:
        recent = list(_spoken) + ([(_busy_since, t, _busy_text)] if _busy > 0 else [])
    from rapidfuzz import fuzz

    h = " ".join(words)
    for s, e, text in recent:
        if not (s - 0.5 <= t <= e + window + ECHO_TAIL):
            continue
        said = " ".join(re.findall(r"[a-z0-9']+", text.lower()))
        if said and len(h) <= len(said) + 12 and fuzz.partial_ratio(h, said) >= 85:
            return True
    return False


def hush_local() -> int:
    """Stop the hub's speaker mid-word. Returns how many sounds were cut off."""
    with _busy_lock:
        procs = list(_procs)
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
    return len(procs)


def _speaking(on: bool) -> None:
    global _busy, _quiet_until, _busy_since, _busy_text
    with _busy_lock:
        now = time.monotonic()
        if on:
            if _busy == 0:
                _busy_since, _busy_text = now, speaking_text
            else:
                _busy_text = f"{_busy_text} {speaking_text}" if speaking_text not in _busy_text else _busy_text
            _busy += 1
            return
        _busy = max(0, _busy - 1)
        if _busy == 0:
            _local_spoken.append((_busy_since, now, _busy_text))
            _spoken.append((_busy_since, now, _busy_text))
        _quiet_until = now + ECHO_TAIL  # the sound's still arriving, and the room echoes


def _far_end(path: Path, volume: float) -> None:
    """Tell the echo canceller what's about to come out of the hub's speaker (hearing: {echo_cancel})."""
    from .hearing_plus import far_end

    if far_end.listening:
        try:
            far_end.playing(convert(path, 16000, 1, volume))
        except Exception as e:
            log.debug("echo reference: %s", e)


def convert(audio: Path, rate: int, channels: int, volume: float = 1.0) -> bytes:
    """Any audio file -> raw 16-bit PCM (needs ffmpeg)."""
    cmd = ["ffmpeg", "-loglevel", "error", "-i", str(audio)]
    if abs(volume - 1.0) > 0.01:
        cmd += ["-af", f"volume={volume:.2f}"]
    cmd += ["-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-"]
    return subprocess.run(cmd, capture_output=True, timeout=60, check=True).stdout


class AudioRouter:
    def __init__(self, home) -> None:
        self.home = home
        vcfg = home.config.voice
        local = home.config.sources.get("local_mic") or {}
        default_player = ["afplay", "{file}"] if sys.platform == "darwin" else ["aplay", "-q", "{file}"]
        self.player = vcfg.get("player", default_player)
        self.play_local = vcfg.get("local", True)
        self.fallback_local = vcfg.get("fallback", "local") == "local"
        self.group_backend = vcfg.get("speakers")  # also speak un-roomed speech on the speaker group
        self._warned = False

    @property
    def local_room(self) -> str | None:
        """The room the hub itself is in (read live: the laptop can be moved while testing)."""
        local = self.home.config.sources.get("local_mic") or {}
        return str(self.home.config.voice.get("local_room") or local.get("room") or "").lower() or None

    def _hub(self, name: str | None = None):
        names = [name] if name else [n for n, b in self.home.config.backends.items()
                                     if b["type"] == "speakers"]
        for n in names:
            try:
                return self.home.backend(n).hub
            except Exception:
                continue
        return None

    def rooms(self) -> set[str]:
        """Every room we know how to talk to."""
        out = {r for i in self.home.instances.all.values() for r in i.rooms}
        if self.home.satellites:
            out |= set(self.home.satellites.speaker_rooms())
        hub = self._hub()
        if hub:
            out |= set(hub.members)
        if self.local_room:
            out.add(self.local_room)
        return out

    def _ffmpeg(self) -> bool:
        if shutil.which("ffmpeg"):
            return True
        if not self._warned:
            log.warning("ffmpeg isn't installed: speech can only play on the hub's own speaker")
            self._warned = True
        return False

    def route(self, room: str | None) -> str:
        """Where speech for this room would go: satellite, speaker, local (the hub) or none."""
        if room and room != self.local_room and self._ffmpeg():
            sats = self.home.satellites
            if sats and sats.has_speaker(room):
                return "satellite"
            hub = self._hub()
            if hub and room in hub.members:
                return "speaker"
            if not self.fallback_local:
                return "none"
        return "local"

    def play(self, audio: Path, room: str | None, volume: float = 1.0, route: str | None = None) -> str:
        """Play in one room (or the default place if room is None). Returns where it went."""
        route = route or self.route(room)
        if route == "satellite":
            self.home.satellites.send_voice(room, convert(audio, 16000, 1, volume))
            return "satellite"
        if route == "speaker":
            self._hub().announce(convert(audio, 44100, 2, volume), names=[room])
            return "speaker"
        if route == "none":
            log.info("no speaker in %s; not played", room)
            return "none"
        self._play_local(audio, volume)
        if room is None and self.group_backend and self._ffmpeg():
            hub = self._hub(self.group_backend)
            if hub:
                hub.announce(convert(audio, 44100, 2, volume))
        return "local"

    def _play_local(self, audio: Path, volume: float) -> None:
        if not (self.play_local and self.player):
            return
        if getattr(self, "_no_player", False):
            return  # said why once already
        path, tmp = audio, None
        if abs(volume - 1.0) > 0.01 and shutil.which("ffmpeg"):
            tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(audio), "-af",
                            f"volume={volume:.2f}", str(tmp)], capture_output=True, timeout=60)
            path = tmp
        _speaking(True)
        proc = None
        try:
            _far_end(path, volume)
            cmd = [str(c).replace("{file}", str(path)) for c in self.player]
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError:  # e.g. no aplay: say so once, not an error for every line
                self._no_player = True
                log.warning("can't play sound on this computer: %s isn't installed (%s), or set voice: {player: [...]}",
                            cmd[0], "sudo apt install alsa-utils" if cmd[0] == "aplay" else "install it")
                return
            with _busy_lock:
                _procs.add(proc)
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            if proc is not None:
                with _busy_lock:
                    _procs.discard(proc)
            _speaking(False)
            if tmp:
                tmp.unlink(missing_ok=True)
