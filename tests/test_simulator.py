"""The virtual house: simulated device nodes from the example config, typed commands."""

import time
from pathlib import Path

import requests

from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.sensors import SensorWatcher
from phrasehome.simulator import prepare, scan_usage
from phrasehome.web import WebServer
from tests.test_house import wait_for

ROOT = Path(__file__).resolve().parents[1]


def test_scan_usage():
    data = {"commands": [{"action": {"node": "{here}", "channel": "lights", "state": True}},
                         {"action": {"node": "kitchen", "channel": "lamp", "level": 20}}],
            "rooms": {"occupancy": {"kitchen": {"node": "kitchen", "input": "presence"}}},
            "automations": [{"when": {"event": "sensor", "node": "garden", "input": "soil", "below": 20}}]}
    u = scan_usage(data, ["kitchen", "garden"])
    assert u["kitchen"]["channels"] == {"lights": "switch", "lamp": "dimmable"}
    assert u["garden"]["channels"] == {"lights": "switch"}          # {here} applies everywhere
    assert u["kitchen"]["inputs"] == {"presence": "digital"} and u["garden"]["inputs"] == {"soil": "analog"}


def test_virtual_house_end_to_end(tmp_path):
    cfg = load_config(ROOT / "config.smarthome.example.yaml")
    cfg.data_dir = str(tmp_path)
    sims = prepare(cfg, base_port=0)
    assert cfg.backends["google"] == {"type": "log"} and cfg.backends["phone"] == {"type": "log"}
    assert "lock" in sims["front door"].channels and "contact" in sims["front door"].inputs
    assert {"lights", "feeder"} <= set(sims["kitchen"].channels)
    home = Dispatcher(cfg)
    cfg.web = {"port": 0, "host": "127.0.0.1"}
    web = WebServer(home)
    try:
        # a typed command, as if heard in the kitchen
        assert web.heard("lights on", "kitchen")["matched"] == "lights_on"
        assert wait_for(lambda: sims["kitchen"].channels["lights"]["on"])
        assert web.heard("lock the front door", None)["matched"] == "lock_door"
        assert wait_for(lambda: sims["front door"].channels["lock"]["on"])
        assert web.heard("make the kitchen purple", "kitchen")["matched"] is None
        # flipping a simulated sensor reaches the hub as an event
        w = SensorWatcher(home)
        node = cfg.backends["esp"]["nodes"]["front door"]
        w.poll_node("front door", node)
        requests.post(f"http://{node}/api/input", params={"name": "contact", "on": "0"}, timeout=3)
        w.poll_node("front door", node)
        (ev,) = home.events.query("sensor")
        assert (ev["node"], ev["input"], ev["state"]) == ("front door", "contact", "off")
        # timed switching ("for") flips back by itself
        requests.post(f"http://{cfg.backends['esp']['nodes']['garden']}/api/set",
                      params={"channel": "pump", "state": "on", "for": "0.1"}, timeout=3)
        assert sims["garden"].channels["pump"]["on"]
        time.sleep(0.25)
        assert not sims["garden"].channels["pump"]["on"]
        # a Bluetooth tag "put" in the bedroom shows up in that node's status
        bed = cfg.backends["esp"]["nodes"]["bedroom"]
        requests.post(f"http://{bed}/api/tag", params={"tag": "keys", "here": "1"}, timeout=3)
        assert requests.get(f"http://{bed}/api/status", timeout=3).json()["ble"][0]["rssi"] == -55
    finally:
        home.close()
        for s in sims.values():
            s.close()
