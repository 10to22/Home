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
from pathlib import Path

log = logging.getLogger("phrasehome.audio")


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
        self.local_room = str(vcfg.get("local_room") or local.get("room") or "").lower() or None
        default_player = ["afplay", "{file}"] if sys.platform == "darwin" else ["aplay", "-q", "{file}"]
        self.player = vcfg.get("player", default_player)
        self.play_local = vcfg.get("local", True)
        self.fallback_local = vcfg.get("fallback", "local") == "local"
        self.group_backend = vcfg.get("speakers")  # also speak un-roomed speech on the speaker group
        self._warned = False

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

    def play(self, audio: Path, room: str | None, volume: float = 1.0) -> str:
        """Play in one room (or the default place if room is None). Returns where it went."""
        if room and room != self.local_room and self._ffmpeg():
            sats = self.home.satellites
            if sats and sats.has_speaker(room):
                sats.send_voice(room, convert(audio, 16000, 1, volume))
                return "satellite"
            hub = self._hub()
            if hub and room in hub.members:
                hub.announce(convert(audio, 44100, 2, volume), names=[room])
                return "speaker"
            if not self.fallback_local:
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
        path, tmp = audio, None
        if abs(volume - 1.0) > 0.01 and shutil.which("ffmpeg"):
            tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(audio), "-af",
                            f"volume={volume:.2f}", str(tmp)], capture_output=True, timeout=60)
            path = tmp
        try:
            cmd = [str(c).replace("{file}", str(path)) for c in self.player]
            subprocess.run(cmd, capture_output=True, timeout=120)
        finally:
            if tmp:
                tmp.unlink(missing_ok=True)
