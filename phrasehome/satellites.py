"""Receive audio from ESP32 mic satellites over UDP.

Packet from satellite -> hub:
    b"PHM1" | u8 room_len | room (utf-8) | PCM: 16 kHz, 16-bit little-endian, mono
Packet from hub -> satellite (LED feedback):
    b"PHA1" | u8 status    (1 = heard a command, 2 = it worked, 3 = it failed)
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Callable

log = logging.getLogger("phrasehome.satellites")

MAGIC_AUDIO = b"PHM1"
MAGIC_ACK = b"PHA1"
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
            parsed = parse_packet(data)
            if not parsed:
                continue
            room, pcm = parsed
            if (addr[0], room) not in self._seen:
                self._seen.add((addr[0], room))
                log.info("satellite connected: %s (%s)", room, addr[0])

            def reply(status: str, addr=addr) -> None:
                self.sock.sendto(ack_packet(status), addr)

            self.on_audio(f"{addr[0]}/{room}", room, pcm, reply)

    def close(self) -> None:
        self.sock.close()
