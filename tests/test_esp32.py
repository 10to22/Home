"""Hub <-> ESP32 tests, using fakes instead of hardware."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from phrasehome.actions.esp32 import Esp32Backend
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.listener import handle_result
from phrasehome.matcher import Matcher, expand
from phrasehome.satellites import SatelliteServer, ack_packet, parse_packet

ROOT = Path(__file__).resolve().parents[1]


# ---------- fake ESP32 device node (same HTTP API as firmware/device_node) ----------
class FakeNode:
    def __init__(self, channels, token=""):
        self.state = {c: {"on": False, "level": 100} for c in channels}
        self.token = token
        node = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if node.token and self.headers.get("X-Token") != node.token:
                    return self._send(401, {"error": "bad token"})
                ch = node.state.get(q.get("channel"))
                if u.path != "/api/set" or ch is None:
                    return self._send(404, {"error": "no such channel"})
                st = q.get("state")
                if st == "toggle":
                    ch["on"] = not ch["on"]
                elif st:
                    ch["on"] = st == "on"
                if "level" in q:
                    ch["level"] = int(q["level"])
                    if not st:
                        ch["on"] = ch["level"] > 0
                self._send(200, node.state)

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def nodes():
    kitchen = FakeNode(["lights", "lamp"], token="s3cret")
    bedroom = FakeNode(["lights"], token="s3cret")
    yield kitchen, bedroom
    kitchen.close()
    bedroom.close()


def backend(nodes):
    k, b = nodes
    return Esp32Backend("esp", {"nodes": {"kitchen": k.host, "bedroom": b.host}, "token": "s3cret"}, None)


def test_switch_and_dim(nodes):
    k, _ = nodes
    be = backend(nodes)
    be.run({"node": "kitchen", "channel": "lights", "state": True}, "")  # YAML `on` == True
    assert k.state["lights"]["on"] is True
    be.run({"node": "kitchen", "channel": "lamp", "level": "40"}, "")
    assert k.state["lamp"] == {"on": True, "level": 40}
    be.run({"node": "Kitchen", "channel": "lights", "state": "toggle"}, "")
    assert k.state["lights"]["on"] is False


def test_all_nodes_skips_missing_channel(nodes):
    k, b = nodes
    be = backend(nodes)
    be.run({"node": "all", "channel": "lights", "state": "on"}, "")
    assert k.state["lights"]["on"] and b.state["lights"]["on"]
    with pytest.raises(RuntimeError):
        be.run({"node": "bedroom", "channel": "lamp", "state": "on"}, "")


def test_bad_token(nodes):
    k, b = nodes
    be = Esp32Backend("esp", {"nodes": {"kitchen": k.host}, "token": "wrong"}, None)
    with pytest.raises(RuntimeError):
        be.run({"node": "kitchen", "channel": "lights", "state": "on"}, "")


def test_validation(nodes):
    be = backend(nodes)
    with pytest.raises(ValueError):
        be.run({"node": "garage", "channel": "lights", "state": "on"}, "")
    with pytest.raises(ValueError):
        be.run({"node": "kitchen", "channel": "lights", "state": "blue"}, "")


# ---------- satellite protocol ----------
def test_packet_parse():
    pcm = b"\x01\x00" * 10
    pkt = b"PHM1" + bytes([11]) + b"Living Room" + pcm
    assert parse_packet(pkt) == ("living room", pcm)
    assert parse_packet(b"junk") is None
    assert parse_packet(b"PHM1" + bytes([0]) + pcm) is None
    assert ack_packet("done") == b"PHA1\x02"


def test_udp_roundtrip():
    got = []
    srv = SatelliteServer(lambda src, room, pcm, reply: (got.append((room, pcm)), reply("matched")),
                          host="127.0.0.1", port=0)
    srv.start()
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c.settimeout(2)
    c.sendto(b"PHM1" + bytes([7]) + b"kitchen" + b"\x00\x00" * 4, ("127.0.0.1", srv.port))
    assert c.recvfrom(16)[0] == b"PHA1\x01"
    assert got == [("kitchen", b"\x00\x00" * 4)]
    srv.close()


# ---------- room-aware commands + arbitration, end to end through the dispatcher ----------
def esp_config(tmp_path, nodes, arbitration=0.3):
    k, b = nodes
    p = tmp_path / "c.yaml"
    p.write_text(f"""
settings: {{ arbitration_seconds: {arbitration}, cooldown_seconds: 0 }}
sources: {{ satellites: {{}} }}
backends:
  esp: {{ type: esp32, token: s3cret, nodes: {{ kitchen: "{k.host}", bedroom: "{b.host}" }} }}
commands:
  - phrases: [lights on]
    action: {{ node: "{{here}}", channel: lights, state: on }}
""")
    return load_config(p)


def run_heard(cfg, heard):
    s = cfg.settings
    m = Matcher(expand(cfg.commands, cfg.slots), s.match_mode, s.match_threshold, s.block_words)
    d = Dispatcher(cfg)
    statuses = []
    for room, text, conf in heard:
        result = {"text": text, "result": [{"conf": conf}] * len(text.split())}
        handle_result(result, m, d, cfg, room=room, reply=lambda st, r=room: statuses.append((r, st)))
    time.sleep(cfg.settings.arbitration_seconds + 0.5)
    d.close()
    return statuses


def test_here_uses_the_room_that_heard_it(tmp_path, nodes):
    k, b = nodes
    statuses = run_heard(esp_config(tmp_path, nodes), [("bedroom", "lights on", 0.95)])
    assert b.state["lights"]["on"] and not k.state["lights"]["on"]
    assert statuses == [("bedroom", "matched"), ("bedroom", "done")]


def test_two_mics_hear_it_only_clearest_fires(tmp_path, nodes):
    k, b = nodes
    statuses = run_heard(esp_config(tmp_path, nodes),
                         [("kitchen", "lights on", 0.97), ("bedroom", "lights on", 0.7)])
    assert k.state["lights"]["on"] and not b.state["lights"]["on"]
    assert {r for r, _ in statuses} == {"kitchen"}


def test_esp32_example_config_loads():
    cfg = load_config(ROOT / "config.esp32.example.yaml")
    assert set(cfg.sources) == {"satellites", "local_mic"}
    s = cfg.settings
    m = Matcher(expand(cfg.commands, cfg.slots), s.match_mode, s.match_threshold, s.block_words)
    assert m.match("fan off").command.name == "fan_off"
    assert m.match("turn on the living room lights").slots == {"room": "living room"}


def test_speaker_commands_in_example():
    cfg = load_config(ROOT / "config.esp32.example.yaml")
    s = cfg.settings
    m = Matcher(expand(cfg.commands, cfg.slots), s.match_mode, s.match_threshold, s.block_words)
    assert m.match("play everywhere").command.action == {"backend": "music", "speaker": "all", "mode": "group"}
    assert m.match("bedroom speaker bluetooth").slots == {"room": "bedroom"}
    assert m.match("volume fifty percent everywhere").slots == {"level": "50"}
    assert m.match("louder").command.name == "volume_up"


def test_offline_node_is_skipped_quickly(monkeypatch):
    """An unplugged module (or one you don't own yet) fails once, then fails fast for 30 s."""
    import requests

    from phrasehome.actions.esp32 import Esp32Backend

    calls = []

    def boom(method, url, **kw):
        calls.append(url)
        raise requests.ConnectionError("Failed to resolve")

    monkeypatch.setattr(requests, "request", boom)
    b = Esp32Backend("esp", {"nodes": {"front door": "phrasehome-front-door.local"}}, None)
    for _ in range(3):
        with pytest.raises(requests.ConnectionError):
            b.get_state("front door", "lock")
    assert len(calls) == 1  # only the first one actually waited on the network
