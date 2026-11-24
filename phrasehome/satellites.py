"""Receive audio from ESP32 mic satellites over UDP.

Packet from satellite -> hub:
    b"PHM1" | u8 room_len | room (utf-8) | PCM: 16 kHz, 16-bit little-endian, mono
Packet from hub -> satellite (LED feedback):
    b"PHA1" | u8 status    (1 = heard a command, 2 = it worked, 3 = it failed)
Hello from satellite -> hub, every few seconds:
    b"PHC1" | u8 flags (1 = has a speaker, 2 = muted by its button) | u8 room_len | room
Speech from hub -> satellite with a speaker:
    b"PHV1" | u32 seq | PCM: 16 kHz, 16-bit little-endian, mono (512 samples)
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Callable

log = logging.getLogger("phrasehome.satellites")

MAGIC_AUDIO = b"PHM1"
MAGIC_ACK = b"PHA1"
MAGIC_HELLO = b"PHC1"
MAGIC_VOICE = b"PHV1"
VOICE_CHUNK = 1024  # bytes = 512 samples = 32 ms at 16 kHz
FLAG_SPEAKER, FLAG_MUTED = 1, 2
STATUS = {"matched": 1, "done": 2, "failed": 3}


def parse_packet(data: bytes) -> tuple[str, bytes] | None:
    if len(data) < 6 or data[:4] != MAGIC_AUDIO:
        return None
    n = data[4]
    if len(data) < 5 + n:
        return None
    room = data[5 : 5 + n].decode("utf-8", "replace").strip().lower()
    pcm = data[5 + n :]
    if not room or len(pcm) % 2:
        return None
    return room, pcm


def parse_hello(data: bytes) -> tuple[str, int] | None:
    if len(data) < 7 or data[:4] != MAGIC_HELLO:
        return None
    flags, n = data[4], data[5]
    room = data[6: 6 + n].decode("utf-8", "replace").strip().lower()
    return (room, flags) if room else None


def ack_packet(status: str) -> bytes:
    return MAGIC_ACK + bytes([STATUS[status]])


class SatelliteServer:
    """Listens on UDP; calls on_audio(source_id, room, pcm, reply) for each packet."""

    def __init__(
        self,
        on_audio: Callable[[str, str, bytes, Callable[[str], None]], None],
        host: str = "0.0.0.0",
        port: int = 7777,
        allowed_ips: list[str] | None = None,
    ) -> None:
        self.on_audio = on_audio
        self.allowed = set(allowed_ips or [])
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((host, port))
        self.port = self.sock.getsockname()[1]
        self._seen: set[tuple[str, str]] = set()
        self.modules: dict[str, dict] = {}  # room -> {"addr", "flags", "last"}
        self._mute_until: dict[str, float] = {}
        self._voice_seq = 0
        self._send_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        log.info("waiting for ESP32 mic satellites on UDP port %d", self.port)
        self._thread.start()

    def _run(self) -> None:
        while True:
            try:
                data, addr = self.sock.recvfrom(4096)
            except OSError:
                return  # socket closed
            if self.allowed and addr[0] not in self.allowed:
                continue
            hello = parse_hello(data)
            if hello:
                room, flags = hello
                was = self.modules.get(room, {}).get("flags")
                self.modules[room] = {"addr": addr, "flags": flags, "last": time.monotonic()}
                if was is not None and (was & FLAG_MUTED) != (flags & FLAG_MUTED):
                    log.info("satellite %s %s", room, "muted" if flags & FLAG_MUTED else "unmuted")
                continue
            parsed = parse_packet(data)
            if not parsed:
                continue
            room, pcm = parsed
            mod = self.modules.setdefault(room, {"addr": addr, "flags": 0, "last": 0.0})
            mod["addr"], mod["last"] = addr, time.monotonic()
            if self.is_muted(room):
                continue  # it's talking right now: don't let it hear itself
            if (addr[0], room) not in self._seen:
                self._seen.add((addr[0], room))
                log.info("satellite connected: %s (%s)", room, addr[0])

            def reply(status: str, addr=addr) -> None:
                self.sock.sendto(ack_packet(status), addr)

            self.on_audio(f"{addr[0]}/{room}", room, pcm, reply)

    # ---------- talking back ----------
    def has_speaker(self, room: str) -> bool:
        m = self.modules.get(room)
        return bool(m and m["flags"] & FLAG_SPEAKER and time.monotonic() - m["last"] < 30)

    def speaker_rooms(self) -> list[str]:
        return [r for r in self.modules if self.has_speaker(r)]

    def online(self, room: str, within: float = 30) -> bool:
        m = self.modules.get(room)
        return bool(m and time.monotonic() - m["last"] < within)

    def is_muted(self, room: str) -> bool:
        return self._mute_until.get(room, 0) > time.monotonic()

    def send_voice(self, room: str, pcm: bytes) -> None:
        """Stream 16 kHz mono speech to a satellite's speaker, in real time. Blocks until sent."""
        m = self.modules.get(room)
        if not m:
            raise ValueError(f"no satellite in {room}")
        duration = len(pcm) / 32000
        self._mute_until[room] = time.monotonic() + duration + 0.8
        start = time.monotonic()
        with self._send_lock:
            for i, off in enumerate(range(0, len(pcm), VOICE_CHUNK)):
                chunk = pcm[off: off + VOICE_CHUNK].ljust(VOICE_CHUNK, b"\0")
                self._voice_seq = (self._voice_seq + 1) & 0xFFFFFFFF
                self.sock.sendto(MAGIC_VOICE + struct.pack("<I", self._voice_seq) + chunk, m["addr"])
                ahead = start + (i - 8) * 0.032 - time.monotonic()  # stay ~250 ms ahead of playback
                if ahead > 0:
                    time.sleep(ahead)
        self._mute_until[room] = time.monotonic() + max(0.0, duration - (time.monotonic() - start)) + 0.8

    def close(self) -> None:
        self.sock.close()
