"""Room presence, follow-me lights, Bluetooth tags, beds and sleep, sunrise alarm, voice ID,
languages, commute nudges, pets, battery alerts."""

import time
from datetime import datetime, timedelta

import numpy as np
import pytest

from phrasehome import automation, clock
from phrasehome.commute import Commute, traffic_word
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.matcher import Matcher, expand
from phrasehome.pets import Pets
from phrasehome.rooms import RoomTracker
from phrasehome.sensors import SensorWatcher
from phrasehome.voiceid import VoiceID
from tests.test_door import Recorder
from tests.test_house import wait_for

CFG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice:
  engine: none
  phrases:
    hello: { en: "Hello {name}", es: "Hola {name}" }
instances:
  household: { main: true, rooms: [living room, kitchen] }
  sam:
    name: Sam
    rooms: [bedroom]
    sunrise: { minutes: 20, light: { node: bedroom, channel: lamp } }
    commute: { to: "Acme Corp", arrive_by: "09:00", days: daily, minutes: 30, label: work }
  abuela: { name: Abuela, rooms: [abuela room], language: es }
rooms:
  occupancy: { kitchen: { node: kitchen, input: presence } }
  empty_after: 0.05
follow_lights:
  kitchen: { light: { node: kitchen, channel: lights }, only_dark: false, off_after: 0 }
beds: { sam: { node: bedroom, input: bed } }
tags:
  keys: { mac: "AA:00:00:00:00:01", owner: sam, name: "Sam's keys" }
  wallet: { mac: "AA:00:00:00:00:02", owner: sam, carried: true, name: "Sam's wallet" }
  luna: { mac: "AA:00:00:00:00:03", name: Luna }
pets: { luna: { kind: dog, meals_per_day: 2, feeder: { node: kitchen, channel: feeder, for: 2 } } }
voice_id: { threshold: 0.8 }
commands:
  - name: lights
    phrases: { en: [lights on], es: [enciende las luces] }
    action: { node: "{here}", channel: lights, state: on }
  - { name: this_is, phrases: ["this is {person}"], action: { voiceid: enroll, person: "{person}" } }
  - { name: fed, phrases: [i fed the dog], action: { pet: luna, do: fed } }
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
    def __init__(self):
        self.values = {}
        self.offline = set()

    def current(self, node, inp):
        return self.values.get((node, inp))


def test_occupancy_and_lights_follow_you(home):
    r = RoomTracker(home)
    home.sensors = FakeSensors()
    home.sensors.values[("kitchen", "presence")] = {"state": "on"}
    home.events.log("sensor", node="kitchen", input="presence", state="on")
    assert r.is_occupied("kitchen") and home.events.query("room.occupied")
    assert wait_for(lambda: {"node": "kitchen", "channel": "lights", "state": True} in Recorder.calls)
    home.sensors.values[("kitchen", "presence")] = {"state": "off"}
    home.events.log("sensor", node="kitchen", input="presence", state="off")
    time.sleep(0.08)
    home.timeline.run_due()
    assert not r.is_occupied("kitchen") and home.events.query("room.empty")
    assert wait_for(lambda: {"node": "kitchen", "channel": "lights", "state": False} in Recorder.calls)


def test_people_in_rooms_and_tags(home):
    r = RoomTracker(home)
    r.saw("mia", "living room", "voice")
    assert r.people_in("living room") == ["mia"] and r.where("mia")[0] == "living room"
    loc = r.locator
    loc.update("kitchen", [{"mac": "aa:00:00:00:00:01", "rssi": -70}, {"mac": "aa:00:00:00:00:02", "rssi": -60}])
    loc.update("bedroom", [{"mac": "aa:00:00:00:00:01", "rssi": -50}])
    assert loc.where("keys")["room"] == "bedroom"          # strongest signal wins
    assert loc.where("wallet")["room"] == "kitchen"
    assert r.where("sam")[0] == "kitchen"                   # carried wallet -> Sam's location
    assert home.run_action({"backend": "report", "report": "where", "what": "keys"}) == "Sam's keys are in the bedroom."
    assert home.run_action({"backend": "report", "report": "where", "what": "Sam"}) == "Sam is in the kitchen."
    assert "haven't heard" in home.run_action({"backend": "report", "report": "where", "what": "luna"})


def test_bed_sensor_sleep_log(home):
    r = RoomTracker(home)
    night = datetime(2026, 11, 23, 23, 0).astimezone()
    r._bed("sam", True, night)
    assert home.events.query("bed.in") and home.events.query("bed.all_in")
    r._bed("sam", False, night + timedelta(hours=3))            # bathroom trip
    r._bed("sam", True, night + timedelta(hours=3, minutes=10))
    r._bed("sam", False, night + timedelta(hours=7, minutes=30))  # morning
    (entry,) = r.sleep_log["sam"]
    assert entry["in"] == "23:00" and entry["out"] == "06:30" and entry["hours"] == 7.5
    assert r.sleep_report("sam").startswith("Last night: 7 hours 30 minutes")
    assert r.where("sam")[0] == "bedroom"


def test_sunrise_and_alarm_soon(home):
    sam = home.instances.all["sam"]
    due = clock.now() + timedelta(minutes=15)
    home.instances.add(sam, "alarm", due, label="work")
    home.scheduler.pre_alarms(clock.now())
    assert home.events.query("agenda.alarm_soon") == []            # 15 min away: not yet "soon"
    assert len(home.timeline) == 21                                  # sunrise fade scheduled
    home.timeline._heap = [(0, i, f) for _, i, f in home.timeline._heap]
    home.timeline.run_due()
    levels = [c["level"] for c in Recorder.calls if c.get("channel") == "lamp"]
    assert levels[0] == 1 and levels[-1] == 100 and levels == sorted(levels)
    home.scheduler.pre_alarms(due - timedelta(minutes=8))
    (soon,) = home.events.query("agenda.alarm_soon")
    assert soon["instance"] == "sam"
    home.scheduler.pre_alarms(due - timedelta(minutes=7))
    assert len(home.events.query("agenda.alarm_soon")) == 1


def test_voice_id(home):
    v = VoiceID(home)
    rng = np.random.default_rng(5)
    sam, mia = rng.normal(size=128), rng.normal(size=128)
    assert v.identify(sam)[0] is None
    v.identify(sam + rng.normal(scale=0.1, size=128), room="bedroom")   # Sam talks...
    v.identify(sam + rng.normal(scale=0.1, size=128), room="bedroom")
    home.run_action(home.config.commands[1].action, {"here": "bedroom", "person": "sam"})  # "this is Sam"
    assert "Thanks Sam" in home.said[-1][0]
    assert v.identify(sam + rng.normal(scale=0.1, size=128))[0] == "sam"
    assert v.identify(mia)[0] is None
    assert VoiceID(home).prints.keys() == {"sam"}                         # saved


def test_languages(home):
    assert home.language_for("abuela room") == "es" and home.language_for("kitchen") == "en"
    es = Matcher(expand(home.config.commands, home.config.slots, "es"))
    m = es.match("Enciende las luces")
    assert m and m.command.name == "lights"
    assert Matcher(expand(home.config.commands, home.config.slots, "en")).match("enciende las luces") is None
    v = home.voice
    assert v.resolve("hello", lang="es", name="Rosa")[0] == "Hola Rosa"
    assert v.resolve("hello", lang="en", name="Rosa")[0] == "Hello Rosa"


def test_person_slot_is_automatic(home):
    assert home.config.slots["person"] == {"sam": "sam", "abuela": "abuela"}


def test_commute_nudges(home):
    c = Commute(home)
    sam = home.instances.all["sam"]
    home.state.set_presence("sam", "home")
    RoomTracker(home).saw("sam", "kitchen", "voice")
    c.travel = lambda to, mode="drive": {"minutes": 34, "usual_minutes": 24, "traffic": traffic_word(34, 24)}
    morning = clock.now().replace(hour=8, minute=5, second=0, microsecond=0)
    p = c.plan(sam, morning)
    assert p["leave"].strftime("%H:%M") == "08:21" and p["traffic"] == "heavy"
    assert c.summary(sam).startswith("Traffic is heavy: 34 minutes to work. Leave by 8:21 AM")
    c.tick(morning.replace(minute=12))                 # 9 minutes before leaving
    assert "leave in 9 minutes for work" in home.said[-1][0] and home.said[-1][1] == "kitchen"
    c.tick(morning.replace(minute=13))
    assert len(home.said) == 1                         # no repeat
    c.tick(morning.replace(minute=21))
    assert "time to leave for work" in home.said[-1][0]
    assert [e["kind"] for e in home.events.query("commute")] == ["commute.leave_soon", "commute.leave_now"]


def test_pets(home):
    p = Pets(home)
    assert p.status("dog") == "Nobody has fed luna today."
    home.run_action(home.config.commands[2].action, {"here": "bedroom"})
    assert home.said[-1][0] == "Got it, luna has been fed."
    assert p.log["luna"][-1]["by"] == "sam"                # said in Sam's room
    p.feed("luna")
    assert {"node": "kitchen", "channel": "feeder", "state": True, "for": 2} in Recorder.calls
    assert "2 meals today, so luna is done for the day" in p.status()


def test_battery_alert_once(home):
    w = SensorWatcher(home)
    for pct in (50, 18, 15, 12):
        w.poll_node("mailbox", "x", {"inputs": [], "battery": pct})
    assert [e["value"] for e in home.events.query("device.battery_low")] == [18]
    w.poll_node("mailbox", "x", {"battery": 95})
    w.poll_node("mailbox", "x", {"battery": 10})
    assert len(home.events.query("device.battery_low")) == 2


def test_ble_from_device_node_status(home):
    RoomTracker(home)
    w = SensorWatcher(home)
    w.poll_node("living room", "x", {"inputs": [], "ble": [{"mac": "AA:00:00:00:00:03", "rssi": -61}]})
    assert home.locator.where("luna")["room"] == "living room"
