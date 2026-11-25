"""simulate --mic --webcam: moving the laptop's microphone and webcam between rooms."""

import time
from pathlib import Path

import numpy as np
import pytest

from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.simulator import WEBCAM, Placement, prepare
from tests.test_house import wait_for

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def house(tmp_path, monkeypatch):
    frames = []

    class FakeWebcam:
        def __init__(self, spec):
            assert spec == 0  # the laptop's webcam, opened once

        def read(self):
            frames.append(1)
            return np.full((48, 64, 3), len(frames) % 255, np.uint8)

    monkeypatch.setattr("phrasehome.door_scanner.Camera", FakeWebcam)
    cfg = load_config(ROOT / "config.smarthome.example.yaml")
    cfg.data_dir = str(tmp_path)
    cfg.extra["sim"] = {"mic": True, "camera": None}
    cfg.sources.setdefault("local_mic", {})["room"] = "living room"
    sims = prepare(cfg, base_port=0, webcam=True)
    home = Dispatcher(cfg)
    home.voice.dry_run = True
    from phrasehome.vision import CameraHub

    home.cameras = CameraHub(home)
    home.cameras.start()
    place = Placement(home)
    place.interval = 0.02
    place.start()
    yield home, place, frames
    place.stop()
    home.cameras.stop()
    home.close()
    for s in sims.values():
        s.stop() if hasattr(s, "stop") else None


def test_every_camera_waits_for_the_webcam(house):
    home, place, _ = house
    cams = home.config.cameras
    assert cams and all(c["url"] == WEBCAM for c in cams.values())
    assert place.camera == "front door"  # starts as the door camera (face scanning)
    assert home.config.door_scanners[0]["camera"] == "front door"


def test_webcam_picture_goes_where_its_placed(house):
    home, place, _ = house
    door, yard = home.cameras.get("front door"), home.cameras.get("back yard")
    assert wait_for(lambda: door.online())
    assert not yard.online()
    place.move_camera("back yard")
    t = yard.time
    assert wait_for(lambda: yard.time > t)
    before = door.time
    time.sleep(0.15)
    assert door.time == before  # the door camera stopped getting pictures
    place.move_camera(None)  # "unplugged"
    t = yard.time
    time.sleep(0.15)
    assert yard.time == t
    with pytest.raises(ValueError, match="unknown camera"):
        place.move_camera("attic")


def test_moving_the_microphone(house):
    home, place, _ = house
    assert place.mic_room == "living room" and "bedroom" in place.rooms()
    place.move_mic("Bedroom")
    assert home.config.sources["local_mic"]["room"] == "bedroom"
    assert home.voice.router.local_room == "bedroom"  # its speaker moved with it
    assert home.default_room() == "bedroom"  # dashboard buttons with {here} follow it too
    assert home.events.query("sim.moved")[-1]["room"] == "bedroom"
    with pytest.raises(ValueError, match="unknown room"):
        place.move_mic("narnia")


def test_dashboard_card(house):
    from phrasehome.web import WebServer

    home, place, _ = house
    home.sim = place
    web = WebServer.__new__(WebServer)
    web.home = home
    out = web.handle_post("/api/sim", {"mic_room": "kitchen", "camera": "driveway"})["sim"]
    assert out["mic_room"] == "kitchen" and out["camera"] == "driveway" and out["mic"] and out["webcam"]
    assert "front door" in out["cameras"] and "kitchen" in out["rooms"]
    web.handle_post("/api/sim", {"camera": ""})
    assert place.camera is None


def test_laptop_mic_uses_the_room_it_was_moved_to(monkeypatch):
    import sys
    import types

    from phrasehome import audio_out, listener

    cb = {}

    class Stream:
        def __init__(self, **kw):
            cb["f"] = kw["callback"]

        def start(self):
            pass

    monkeypatch.setattr(audio_out, "_busy", 0)
    monkeypatch.setattr(audio_out, "_quiet_until", 0.0)
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=Stream))
    opts = {"room": "living room"}
    got = []
    listener._open_local_mic(types.SimpleNamespace(settings=types.SimpleNamespace(sample_rate=16000, device=None)),
                             opts, got.append)
    cb["f"](b"\x10\x27" * 100, 100, None, None)
    opts["room"] = "bedroom"  # what Placement.move_mic does
    cb["f"](b"\x10\x27" * 100, 100, None, None)
    assert [g[1] for g in got] == ["living room", "bedroom"]
