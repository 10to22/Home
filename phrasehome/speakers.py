"""Multi-room music for ESP32 speaker modules (firmware/speaker).

Each speaker is in one of two modes:
  bluetooth - a normal Bluetooth speaker; pair your phone with it directly
  group     - plays the hub's music stream in sync with the other group speakers

The hub's music comes from any command that writes raw audio (16-bit LE, 44.1 kHz, stereo) to
stdout. By default it records whatever the Pi is playing, which includes a phone connected to the
Pi over Bluetooth (deploy/setup-bluetooth-sink.sh). Spotify Connect (librespot) or ffmpeg work too.

Wire protocol (UDP):
  hub -> speaker  :7779  b"PHS1" | u32 seq | i64 play_at_us | PCM (256 stereo frames)
  speaker -> hub  :7778  b"PHT1" | u64 speaker_us                  (clock sync request)
  hub -> speaker         b"PHT1" | u64 speaker_us | i64 hub_us      (reply)
Speakers keep their clocks in step with the hub, buffer a little audio, and play each packet at
`play_at_us`, so speakers in different rooms stay in sync.
"""

from __future__ import annotations

import logging
import os
import shlex
import socket
import struct
import subprocess
import threading
import time
from typing import Any

import requests

log = logging.getLogger("phrasehome.speakers")

RATE = 44100
CHANNELS = 2
FRAMES_PER_PACKET = 256
BYTES_PER_PACKET = FRAMES_PER_PACKET * CHANNELS * 2
PACKET_US = FRAMES_PER_PACKET * 1_000_000 / RATE
AUDIO_HDR = struct.Struct("<4sIq")
TIME_REQ = struct.Struct("<4sQ")
TIME_REPLY = struct.Struct("<4sQq")
DEFAULT_SOURCE = "parec --raw --format=s16le --rate=44100 --channels=2 -d @DEFAULT_MONITOR@"


def hub_us() -> int:
    return time.monotonic_ns() // 1000


def audio_packet(seq: int, play_at: int, pcm: bytes) -> bytes:
    return AUDIO_HDR.pack(b"PHS1", seq & 0xFFFFFFFF, play_at) + pcm


def time_reply(request: bytes) -> bytes | None:
    if len(request) < TIME_REQ.size:
        return None
    magic, t0 = TIME_REQ.unpack_from(request)
    if magic != b"PHT1":
        return None
    return TIME_REPLY.pack(b"PHT1", t0, hub_us())


class SpeakerHub:
    def __init__(self, name: str, cfg: dict[str, Any], config=None) -> None:
        self.name = name
        self.speakers = {str(k).lower(): str(v) for k, v in (cfg.get("speakers") or {}).items()}
        if not self.speakers:
            raise ValueError(f"backend '{name}': add at least one speaker under `speakers:`")
        self.source = cfg.get("source", DEFAULT_SOURCE)
        self.latency_us = int(float(cfg.get("latency_ms", 300)) * 1000)
        self.audio_port = int(cfg.get("audio_port", 7779))
        self.time_port = int(cfg.get("time_port", 7778))
        self.poll_seconds = float(cfg.get("poll_seconds", 5))
        token = cfg.get("token") or os.environ.get(cfg.get("token_env", "PHRASEHOME_TOKEN"), "")
        self.headers = {"X-Token": token} if token else {}
        self.timeout = float(cfg.get("timeout", 4))
        self.members: dict[str, str] = {}  # speakers currently in group mode: name -> ip
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._running = False
        self._proc: subprocess.Popen | None = None
        self._announce = bytearray()  # speech waiting to be played on the group

    # ---------- speaker control (HTTP) ----------
    def _targets(self, speaker: str) -> list[tuple[str, str]]:
        speaker = speaker.lower()
        if speaker == "all":
            return list(self.speakers.items())
        if speaker not in self.speakers:
            raise ValueError(f"unknown speaker '{speaker}' (known: {', '.join(self.speakers)})")
        return [(speaker, self.speakers[speaker])]

    def _post(self, host: str, path: str, **params) -> dict:
        r = requests.post(f"http://{host}{path}", params=params, headers=self.headers,
                          timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def status(self, speaker: str = "all") -> dict[str, dict | str]:
        out: dict[str, dict | str] = {}
        for name, host in self._targets(speaker):
            try:
                r = requests.get(f"http://{host}/api/status", timeout=self.timeout)
                r.raise_for_status()
                out[name] = r.json()
            except Exception as e:
                out[name] = f"offline ({e.__class__.__name__})"
        return out

    def set_mode(self, speaker: str, mode: str) -> list[str]:
        if mode not in ("bluetooth", "group"):
            raise ValueError("mode must be bluetooth or group")
        done = [n for n, h in self._each(speaker, lambda h: self._post(h, "/api/mode", mode=mode))]
        self._wake.set()  # re-check who's in the group soon (speakers reboot to switch modes)
        return done

    def set_volume(self, speaker: str, volume: Any) -> list[str]:
        v = str(volume).strip().lower()
        if v in ("up", "louder"):
            params = {"change": 10}
        elif v in ("down", "quieter", "softer"):
            params = {"change": -10}
        else:
            params = {"level": max(0, min(100, int(float(v))))}
        return [n for n, _ in self._each(speaker, lambda h: self._post(h, "/api/volume", **params))]

    def _each(self, speaker: str, fn):
        ok, errors = [], []
        for name, host in self._targets(speaker):
            try:
                ok.append((name, fn(host)))
            except Exception as e:
                errors.append(f"{name}: {e}")
        if errors and not ok:
            raise RuntimeError("; ".join(errors))
        for e in errors:
            log.warning("speaker %s", e)
        return ok

    def announce(self, pcm: bytes) -> None:
        """Play speech (44.1 kHz 16-bit stereo) on every group speaker, in place of the music."""
        with self._lock:
            if not self.members:
                return
            self._announce += pcm

    def _next_announcement(self) -> bytes | None:
        with self._lock:
            if not self._announce:
                return None
            chunk = bytes(self._announce[:BYTES_PER_PACKET])
            del self._announce[:BYTES_PER_PACKET]
        return chunk.ljust(BYTES_PER_PACKET, b"\0")

    # ---------- streaming ----------
    def start(self) -> None:
        self._running = True
        self.time_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.time_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.time_sock.bind(("0.0.0.0", self.time_port))
        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for fn in (self._time_server, self._poll_members, self._stream):
            threading.Thread(target=fn, daemon=True).start()
        log.info("speaker hub on: %d speakers, music source: %s", len(self.speakers), self.source)

    def close(self) -> None:
        self._running = False
        self._wake.set()
        self._stop_source()
        for s in ("time_sock", "audio_sock"):
            if hasattr(self, s):
                getattr(self, s).close()

    def _time_server(self) -> None:
        while self._running:
            try:
                data, addr = self.time_sock.recvfrom(64)
            except OSError:
                return
            reply = time_reply(data)
            if reply:
                self.time_sock.sendto(reply, addr)

    def _poll_members(self) -> None:
        while self._running:
            members = {}
            for name, info in self.status().items():
                if isinstance(info, dict) and info.get("mode") == "group":
                    host = self.speakers[name].split(":")[0]
                    try:
                        members[name] = socket.gethostbyname(host)
                    except OSError:
                        continue
            with self._lock:
                if set(members) != set(self.members):
                    log.info("speaker group: %s", ", ".join(sorted(members)) or "(empty)")
                self.members = members
            self._wake.wait(self.poll_seconds)
            if self._wake.is_set():
                self._wake.clear()
                time.sleep(4)  # give a rebooting speaker time to come back

    def _start_source(self) -> subprocess.Popen:
        log.info("starting music source")
        return subprocess.Popen(shlex.split(self.source), stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=0)

    def _stop_source(self) -> None:
        if self._proc:
            self._proc.kill()
            self._proc.wait()
            self._proc = None

    def _read_packet(self) -> bytes | None:
        buf = b""
        while len(buf) < BYTES_PER_PACKET:
            chunk = self._proc.stdout.read(BYTES_PER_PACKET - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _stream(self) -> None:
        seq = 0
        anchor = n = 0
        while self._running:
            with self._lock:
                targets = list(self.members.values())
            if not targets:
                self._stop_source()  # nobody listening: don't burn CPU or Wi-Fi
                time.sleep(0.5)
                continue
            if self._proc is None:
                try:
                    self._proc = self._start_source()
                except OSError as e:
                    log.error("music source failed to start: %s", e)
                    time.sleep(5)
                    continue
                anchor, n = hub_us(), 0
            pcm = self._read_packet()
            if pcm is None:
                log.warning("music source stopped; restarting in 2 s")
                self._stop_source()
                time.sleep(2)
                continue
            now = hub_us()
            due = anchor + int(n * PACKET_US)
            if now > due + 200_000:  # source paused or stalled: start a fresh timeline
                anchor, n, due = now, 0, now
            elif due > now:  # source faster than real time (e.g. a file): pace it
                time.sleep((due - now) / 1e6)
            speech = self._next_announcement()
            if speech is not None:
                pcm = speech  # speech replaces the music for its duration
            pkt = audio_packet(seq, due + self.latency_us, pcm)
            for ip in targets:
                try:
                    self.audio_sock.sendto(pkt, (ip, self.audio_port))
                except OSError:
                    pass
            seq += 1
            n += 1


_HUBS: dict[str, SpeakerHub] = {}


def get_hub(name: str, cfg: dict[str, Any], config=None) -> SpeakerHub:
    if name not in _HUBS:
        _HUBS[name] = SpeakerHub(name, cfg, config)
    return _HUBS[name]
