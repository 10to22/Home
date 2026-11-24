"""Cameras, package watch, weather, smoke listener, gentle alarm, closed report."""

import time

import numpy as np
import pytest

from phrasehome import ai
from phrasehome.alarm import AlarmSystem
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.smoke import SmokeListener
from phrasehome.vision import CameraHub
from phrasehome.weather import Weather
from tests.test_door import Recorder
from tests.test_house import wait_for

CFG = """
data_dir: data
location: { latitude: 40.0, longitude: -75.0 }
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none }
doors:
  front:
    lock: { node: front, channel: lock, state: on }
    unlock: { node: front, channel: lock, state: off }
    contact: { node: front door, input: contact }
    room: front door
cameras:
  porch: { url: "http://x/capture", roles: [door, porch], room: front door }
  yard: { url: "http://y/capture", roles: [perimeter] }
alarm:
  exit_delay: 0.01
  entry_delay: 0.05
  watch: [ { node: front door, input: contact, state: off } ]
weather: { units: fahrenheit, door_contact: { node: front door, input: contact }, reminder_window: "00:00-23:59" }
commands:
  - { name: closed, phrases: [is everything closed], action: { report: closed } }
"""


@pytest.fixture
def home(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.said = []
    h.voice.speak = lambda key, room=None, rooms=None, everywhere=False, urgent=False, **v: \
        h.said.append((key, room, v)) or key
    yield h
    h.close()


class FakeSensors:
    def __init__(self, **values):
        self.values = values
        self.offline = set()

    def current(self, node, inp):
        return self.values.get((node, inp))


frame = np.zeros((120, 160, 3), np.uint8)


# ---------------------------------------------------------------- cameras
def test_detection_events_with_cooldown(home):
    hub = CameraHub(home)
    hub._save = lambda name, f: f"/photos/{name}.jpg"
    person = [{"group": "person", "label": "person", "confidence": 0.9, "box": [0, 0, 5, 5]}]
    car = [{"group": "vehicle", "label": "car", "confidence": 0.8, "box": [0, 0, 5, 5]}]
    assert hub.analyze("yard", frame, person, now=100)[0]["label"] == "person"
    assert hub.analyze("yard", frame, person, now=130) == []          # same visit: quiet
    assert hub.analyze("yard", frame, car + person, now=140)[0]["label"] == "car"
    assert hub.analyze("yard", frame, person, now=300) != []          # a new visit
    evs = home.events.query("camera")
    assert [(e["camera"], e["detected"]) for e in evs] == [("yard", "person"), ("yard", "vehicle"), ("yard", "person")]
    assert evs[0]["photo"] == "/photos/yard.jpg"


def test_package_watch(home):
    hub = CameraHub(home)
    hub._save = lambda name, f: ""
    hub.feeds["porch"].set_frame(frame)
    answers = iter([{"package_visible": True, "description": "a brown box by the mat"},
                    {"package_visible": True, "description": "same box"},
                    {"package_visible": False, "description": ""}])
    hub.check_package = lambda name: next(answers)
    person = [{"group": "person", "label": "person", "confidence": 0.9, "box": [0, 0, 5, 5]}]
    hub.analyze("porch", frame, person, now=1000)          # courier on the porch
    hub.porch_tick(now=1005)                                # still there: no check yet
    assert not home.events.query("package")
    hub.porch_tick(now=1030)                                # left 30 s ago: look
    (ev,) = home.events.query("package.delivered")
    assert ev["description"] == "a brown box by the mat"
    hub.porch_tick(now=1100)                                # not due for a recheck yet
    hub.porch_tick(now=1030 + 16 * 60)                      # recheck: still there
    hub.porch_tick(now=1030 + 32 * 60)                      # recheck: gone, nobody from home
    assert home.events.query("package.taken")


def test_describe_camera(home, monkeypatch):
    hub = CameraHub(home)
    hub.feeds["porch"].set_frame(frame)
    seen = {}

    def fake_ask(h, prompt, images=None, **kw):
        seen["images"], seen["prompt"] = images, prompt
        return "A delivery driver is holding a box."
    monkeypatch.setattr(ai, "ask", fake_ask)
    assert hub.describe("porch") == "A delivery driver is holding a box."
    assert seen["images"][0][:2] == b"\xff\xd8" and "porch camera" in seen["prompt"]
    with pytest.raises(RuntimeError):
        hub.describe("yard")  # no pictures yet


def test_door_scanner_uses_shared_feed(home):
    from phrasehome.door_scanner import FeedCamera

    hub = CameraHub(home)
    cam = FeedCamera(hub.feeds["porch"])
    assert cam.read() is None
    hub.feeds["porch"].set_frame(frame, doorbell=4)
    assert cam.read() is not None and cam.read() is None and cam.doorbell == 4


# ---------------------------------------------------------------- weather
DATA = {"current": {"temperature_2m": 48.2},
        "daily": {"time": ["2026-11-24", "2026-11-25"], "weather_code": [63, 95],
                  "temperature_2m_max": [49.0, 61.0], "temperature_2m_min": [38.0, 50.0],
                  "precipitation_probability_max": [80, 40], "precipitation_sum": [0.4, 1.3],
                  "snowfall_sum": [0, 0], "wind_gusts_10m_max": [20, 55]},
        "hourly": {"weather_code": [3, 63, 63], "precipitation_probability": [10, 80, 80]}}


def test_weather_summary_warnings_and_umbrella(home):
    w = Weather(home)
    w.data = DATA
    w.air = {"current": {"us_aqi": 120, "grass_pollen": 80, "birch_pollen": 5}}
    w.alerts = [{"id": "a1", "event": "Flood Warning", "severity": "Severe", "headline": "Flood Warning until 6 PM"},
                {"id": "a2", "event": "Wind Advisory", "severity": "Moderate", "headline": "Breezy"}]
    s = w.summary()
    assert "rain" in s and "high of 49" in s and "48 degrees now" in s and "80 percent" in s
    assert "unhealthy for sensitive groups" in s and "Grass pollen is high" in s and "Flood Warning" in s
    assert w.needs() == ["umbrella", "jacket"]
    w.check()
    assert [e["event"] for e in home.events.query("weather.alert")] == ["Flood Warning"]
    assert home.events.query("weather.advisory")
    kinds = {e["warning"] for e in home.events.query("weather.warning")}
    assert kinds == {"storm", "heavy rain", "wind"}
    w.check()  # no repeats
    assert len(home.events.query("weather.warning")) == 3
    # leaving through the front door -> one reminder per day
    home.events.log("sensor", node="front door", input="contact", state="off")
    assert wait_for(lambda: any("umbrella" in k for k, *_ in home.said))
    assert home.said[-1][1] == "front door"
    assert w.remind_at_door() is None


# ---------------------------------------------------------------- smoke / CO
def _tone(sec, f=3200, amp=6000):
    t = np.arange(int(16000 * sec)) / 16000
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.int16)


def _quiet(sec):
    return np.random.default_rng(1).normal(0, 40, int(16000 * sec)).astype(np.int16)


def test_smoke_and_co_patterns(home):
    t3 = np.concatenate([_tone(.5), _quiet(.5), _tone(.5), _quiet(.5), _tone(.5), _quiet(1.5)])
    t4 = np.concatenate([np.concatenate([_tone(.1), _quiet(.1)]) for _ in range(4)] + [_quiet(5)])
    s = SmokeListener(home)
    sig = np.tile(t3, 3)
    found = [s.feed("kitchen", sig[i:i + 4000].tobytes()) for i in range(0, len(sig), 4000)]
    assert "smoke" in found
    sig = np.tile(t4, 3)
    found = [s.feed("hall", sig[i:i + 4000].tobytes()) for i in range(0, len(sig), 4000)]
    assert "co" in found
    assert [(e["room"], e["alarm"]) for e in home.events.query("smoke")] == [("kitchen", "smoke"), ("hall", "co")]
    # a microwave beep or a doorbell isn't an alarm
    beep = np.concatenate([_tone(1.2), _quiet(2)] * 3)
    assert not any(s.feed("x", beep[i:i + 4000].tobytes()) for i in range(0, len(beep), 4000))


# ---------------------------------------------------------------- gentle alarm
def test_alarm_entry_grace_then_alert(home):
    a = AlarmSystem(home)
    home.state.set_mode("away")
    assert wait_for(lambda: a.state == "arming")
    time.sleep(0.03)
    home.timeline.run_due()
    assert a.state == "armed"
    home.events.log("sensor", node="front door", input="contact", state="off")
    assert a.state == "entry" and home.said[-1][0] == "alarm_entry"
    time.sleep(0.07)
    home.timeline.run_due()
    assert a.state == "alert"
    assert wait_for(lambda: any(k == "alarm_alert" for k, *_ in home.said))


def test_alarm_disarmed_by_face_scan(home):
    a = AlarmSystem(home)
    a.state = "armed"
    home.state.mode = "away"
    home.events.log("sensor", node="front door", input="contact", state="off")
    assert a.state == "entry"
    home.events.log("scan", result="allowed", name="Sam", door="front")
    assert a.state == "disarmed" and home.state.mode == "home"
    time.sleep(0.07)
    home.timeline.run_due()
    assert a.state == "disarmed"  # the pending alert was cancelled


def test_alarm_by_voice(home):
    AlarmSystem(home)
    home.run_action({"backend": "security", "security": "arm"})
    assert home.state.mode == "away"
    home.run_action({"backend": "security", "security": "disarm"})
    assert home.alarm.state == "disarmed" and home.state.mode == "home"


# ---------------------------------------------------------------- is everything closed
def test_closed_report(home):
    home.sensors = FakeSensors(**{})
    home.sensors.values = {("front door", "contact"): {"state": "on"},
                           ("bedroom", "window"): {"state": "off"}}
    home.doors._remember("front", "locked")
    text = home.run_action(home.config.commands[0].action, {"here": "kitchen"})
    assert text == "Heads up: bedroom window is open."
    home.sensors.values[("bedroom", "window")] = {"state": "on"}
    home.doors._remember("front", "unlocked")
    assert "the front door is unlocked" in home.run_action({"backend": "report", "report": "closed"})
    home.doors._remember("front", "locked")
    assert home.run_action({"backend": "report", "report": "closed"}) == "Everything is closed and locked."
    assert home.run_action({"backend": "report", "report": "closed", "only_problems": True}) == "nothing to report"
