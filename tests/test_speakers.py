"""Speaker hub tests with fake ESP32 speakers (HTTP control + UDP audio)."""

import json
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from phrasehome.actions.speakers import SpeakersBackend
from phrasehome.speakers import (AUDIO_HDR, BYTES_PER_PACKET, PACKET_US, TIME_REPLY, TIME_REQ,
                                 SpeakerHub, hub_us)


class FakeSpeaker:
    """HTTP API like firmware/speaker, plus a UDP socket for group audio."""

    def __init__(self, mode="bluetooth", token=""):
        self.mode, self.volume, self.token = mode, 60, token
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.udp.settimeout(0.2)
        spk = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._send(200, {"name": "x", "mode": spk.mode, "volume": spk.volume, "synced": True})

            def do_POST(self):
                if spk.token and self.headers.get("X-Token") != spk.token:
                    return self._send(401, {})
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/api/mode":
                    spk.mode = q["mode"]
                elif "level" in q:
                    spk.volume = int(q["level"])
                else:
                    spk.volume = max(0, min(100, spk.volume + int(q["change"])))
                self._send(200, {"mode": spk.mode, "volume": spk.volume})

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def packets(self, seconds):
        out, end = [], time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = self.udp.recv(2048)
            except socket.timeout:
                continue
            out.append((hub_us(), AUDIO_HDR.unpack_from(data), len(data) - AUDIO_HDR.size))
        return out

    def close(self):
        self.httpd.shutdown()
        self.udp.close()


@pytest.fixture
def speakers():
    s = {"kitchen": FakeSpeaker("group"), "bedroom": FakeSpeaker("bluetooth")}
    yield s
    for x in s.values():
        x.close()


def hub_cfg(speakers, **extra):
    # A source that produces audio as fast as it can; the hub must pace it to real time.
    gen = f"{sys.executable} -c \"import sys\nwhile True: sys.stdout.buffer.write(bytes(4096))\""
    return {"type": "speakers", "speakers": {n: s.host for n, s in speakers.items()},
            "source": gen, "latency_ms": 300, "time_port": 0, "poll_seconds": 0.2, **extra}


def test_mode_and_volume_control(speakers):
    hub = SpeakerHub("music", hub_cfg(speakers))
    assert hub.set_mode("all", "group") == ["kitchen", "bedroom"]
    assert speakers["bedroom"].mode == "group"
    hub.set_volume("kitchen", "up")
    assert speakers["kitchen"].volume == 70
    hub.set_volume("Kitchen", 25)
    assert speakers["kitchen"].volume == 25
    with pytest.raises(ValueError):
        hub.set_mode("garage", "group")
    with pytest.raises(ValueError):
        hub.set_mode("kitchen", "party")


def test_backend_actions(speakers):
    be = SpeakersBackend("music2", hub_cfg(speakers), None)
    be.run({"speaker": "bedroom", "mode": "group", "volume": "down"}, "")
    assert speakers["bedroom"].mode == "group" and speakers["bedroom"].volume == 50


def test_time_sync_reply():
    hub = SpeakerHub("t", {"speakers": {"x": "127.0.0.1:1"}, "time_port": 0, "poll_seconds": 60})
    hub.start()
    try:
        port = hub.time_sock.getsockname()[1]
        c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        c.settimeout(2)
        c.sendto(TIME_REQ.pack(b"PHT1", 12345), ("127.0.0.1", port))
        magic, t0, hub_t = TIME_REPLY.unpack(c.recv(64))
        assert (magic, t0) == (b"PHT1", 12345)
        assert abs(hub_t - hub_us()) < 100_000
    finally:
        hub.close()


def test_streams_only_to_group_members_in_real_time(speakers):
    k, b = speakers["kitchen"], speakers["bedroom"]
    cfg = hub_cfg(speakers, audio_port=k.udp.getsockname()[1])
    hub = SpeakerHub("s", cfg)
    hub.start()
    try:
        got = k.packets(1.5)
    finally:
        hub.close()
    assert hub.members == {"kitchen": "127.0.0.1"}  # bedroom is in bluetooth mode
    assert len(got) > 100
    # every packet: full size, consecutive sequence numbers, scheduled ~300 ms ahead
    seqs = [h[1] for _, h, _ in got]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
    assert all(n == BYTES_PER_PACKET for _, _, n in got)
    leads = [h[2] - t for t, h, _ in got[5:]]
    assert 200_000 < min(leads) and max(leads) < 320_000
    # paced to real time: ~172 packets/second, not as fast as the source can write
    span = (got[-1][0] - got[0][0]) / 1e6
    rate = (len(got) - 1) / span
    assert 150 < rate < 195, rate
    # play times advance by exactly one packet each
    gaps = {got[i + 1][1][2] - got[i][1][2] for i in range(len(got) - 1)}
    assert all(abs(g - PACKET_US) <= 1 for g in gaps)
