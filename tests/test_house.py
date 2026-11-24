"""Instances, agenda, scheduler, automations, modes, presence, sensors, fingerprint, doorbell,
dashboard, voice routing: with fakes instead of hardware."""

import json
import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from phrasehome import clock
from phrasehome.assistant import Assistant
from phrasehome.automation import Automations
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.fingerprint import FingerprintDoor
from phrasehome.sensors import SensorWatcher
from phrasehome.web import WebServer
from tests.test_assistant import FakeClient, text, tool
from tests.test_door import Recorder

HOUSE = """
data_dir: data
location: { latitude: 45.52, longitude: -122.68 }
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none }
instances:
  household: { main: true, rooms: [living room, kitchen] }
  sam: { name: Sam, rooms: [bedroom], wake_names: [jarvis], quiet_hours: "00:00-00:01",
         voice: { volume: 70 }, people: [Samuel] }
  kid: { name: Mia, rooms: [mia's room], kid_mode: true }
doors:
  front:
    lock:   { node: front, channel: lock, state: on }
    unlock: { node: front, channel: lock, state: off }
    auto_lock_minutes: 0.002
    contact: { node: front door, input: contact }
    room: front door
slots:
  minutes: { numbers: [1, 90] }
routines:
  lockdown: [ { door: all, state: locked }, { speak: lockdown } ]
  smart:
    - if: { mode: away }
      then: [ { node: all, channel: lights, state: off } ]
      else: [ { speak: "Welcome back" } ]
automations:
  - name: leak
    when: { event: sensor, node: kitchen, input: leak, state: on }
    do: [ { speak: leak_alarm, everywhere: true, urgent: true, room: kitchen } ]
  - name: dry plants
    when: { event: sensor, node: garden, input: soil, below: 30 }
    do: [ { node: garden, channel: pump, state: on } ]
  - name: away motion
    when: { event: sensor, node: hall, input: motion, state: on }
    if: { mode: away }
    do: [ { node: hall, channel: siren, state: on } ]
  - name: left open
    when: { event: sensor, node: front door, input: contact, state: off }
    for: 0.05
    do: [ { speak: door_left_open, door: front } ]
schedules:
  - { name: morning, at: "07:00", days: weekdays, do: [ { node: kitchen, channel: lights, state: on } ] }
  - { name: porch, at: sunset, offset: -10, do: [ { node: porch, channel: lights, state: on } ] }
fingerprint:
  - { door: front, host: "127.0.0.1:1", window_seconds: 30, max_failures: 3 }
commands:
  - name: timer
    phrases: ["set a timer for {minutes} minutes"]
    action: { timer: set, minutes: "{minutes}" }
  - { name: stop_alarm, phrases: [stop the alarm], action: { alarm: stop } }
  - { name: lights, phrases: [lights on], action: { node: "{here}", channel: lights, state: on } }
  - { name: front_lock, phrases: [lock the door], action: { door: front, state: locked } }
"""


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture
def home(tmp_path):
    (tmp_path / "c.yaml").write_text(HOUSE)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.said = []
    h.voice.speak = lambda key, room=None, rooms=None, everywhere=False, urgent=False, **v: \
        h.said.append((key, room, rooms, everywhere, v)) or key
    yield h
    h.close()


def keys(h):
    return [k for k, *_ in h.said]


# ---------------------------------------------------------------- instances
def test_rooms_and_permissions(home):
    insts = home.instances
    sam, main, mia = insts.all["sam"], insts.main, insts.all["kid"]
    assert insts.for_room("bedroom") is sam and insts.for_room("garage") is main
    assert insts.get("Sam", asker=main) is sam          # the living room can manage Sam
    assert insts.get("samuel", asker=main) is sam        # face-bank name works too
    assert insts.get("", asker=sam) is sam
    with pytest.raises(PermissionError):
        insts.get("mia", asker=sam)                      # Sam can't see Mia's things
    with pytest.raises(KeyError):
        insts.get("bob", asker=main)


def test_lists_shared_and_personal(home):
    insts = home.instances
    sam, main = insts.all["sam"], insts.main
    insts.list_add(sam, "shopping list", ["Milk", "eggs"])
    insts.list_add(main, "shopping", ["milk", "bread"])   # no duplicates
    assert insts.list_get(sam, "shopping") == insts.list_get(main, "shopping") == ["Milk", "eggs", "bread"]
    insts.list_add(sam, "todo", ["homework"])
    assert insts.list_get(main, "todo") == [] and insts.list_get(sam, "todo") == ["homework"]
    insts.list_remove(main, "shopping", ["MILK"])
    assert insts.list_get(sam, "shopping") == ["eggs", "bread"]
    insts.remember(sam, "Sam likes it cold at night")
    assert insts.forget(sam, "cold") == 1 and insts.memories(sam) == []


def test_reminder_fires_in_owners_room_and_repeats(home):
    sam = home.instances.all["sam"]
    past = clock.now() - timedelta(minutes=1)
    home.instances.add(sam, "reminder", past, "take out the trash", repeat="daily")
    home.scheduler.tick()
    assert ("reminder", "bedroom") in [(k, r) for k, r, *_ in home.said]
    said = next(x for x in home.said if x[0] == "reminder")
    assert said[4]["text"] == "take out the trash" and said[4]["name"] == "Sam"
    nxt = home.instances.items(sam)
    assert len(nxt) == 1 and datetime.fromisoformat(nxt[0]["due"]) > clock.now()


def test_weekday_repeat_skips_weekend(home):
    sam = home.instances.all["sam"]
    fri = datetime(2026, 11, 20, 7, 0).astimezone()  # a Friday
    item = home.instances.add(sam, "alarm", fri, label="work", repeat="weekdays")
    nxt = home.instances.complete(sam, item)
    assert datetime.fromisoformat(nxt["due"]).weekday() < 5


def test_voice_timer_rings_and_stops(home):
    home.run_action(home.config.commands[0].action, {"minutes": "5", "here": "kitchen"})
    assert keys(home) == ["timer_set"] and home.said[0][4]["duration"] == "5 minutes"
    main = home.instances.main
    (item,) = home.instances.items(main, ("timer",))
    assert item["room"] == "kitchen"
    home.run_action({"backend": "timer", "timer": "status"}, {"here": "kitchen"})
    assert home.said[-1][0] == "timer_status"
    home.scheduler.tick(clock.now() + timedelta(minutes=6))
    assert "timer_sound" in keys(home) and "kitchen" in home.scheduler.ringers
    home.run_action({"backend": "alarm", "alarm": "stop"}, {"here": "kitchen"})
    assert not home.scheduler.ringers


def test_alarm_snooze(home):
    sam = home.instances.all["sam"]
    home.instances.add(sam, "alarm", clock.now() - timedelta(seconds=1), label="wake up")
    home.scheduler.tick()
    assert "bedroom" in home.scheduler.ringers
    home.run_action({"backend": "alarm", "alarm": "snooze", "minutes": 9}, {"here": "bedroom"})
    assert not home.scheduler.ringers
    (again,) = home.instances.items(sam, ("alarm",))
    assert 8 <= (datetime.fromisoformat(again["due"]) - clock.now()).total_seconds() / 60 <= 9.1


def test_number_slots_and_matching(home):
    from phrasehome.matcher import Matcher, expand

    m = Matcher(expand(home.config.commands, home.config.slots)).match("set a timer for twenty five minutes")
    assert m.slots == {"minutes": "25"}


# ---------------------------------------------------------------- schedules and automations
def test_schedules_time_days_and_sunset(home):
    mon_7 = datetime(2026, 11, 23, 7, 0).astimezone()
    home.scheduler.run_schedules(mon_7)
    assert wait_for(lambda: {"node": "kitchen", "channel": "lights", "state": True} in Recorder.calls)
    Recorder.calls.clear()
    home.scheduler.run_schedules(datetime(2026, 11, 22, 7, 0).astimezone())  # Sunday: skipped
    time.sleep(0.1)
    assert Recorder.calls == []
    porch = next(s for s in home.config.schedules if s["name"] == "porch")
    when = home.scheduler.schedule_time(porch, datetime(2026, 6, 21).date())
    _, sunset = clock.sun_times(datetime(2026, 6, 21).date(), 45.52, -122.68, when.tzinfo)
    assert when == sunset - timedelta(minutes=10)


def test_automation_with_mode_condition(home):
    home.events.log("sensor", node="hall", input="motion", state="on")
    time.sleep(0.15)
    assert Recorder.calls == []                       # not away: nothing
    home.state.set_mode("away")
    home.events.log("sensor", node="hall", input="motion", state="on")
    assert wait_for(lambda: {"node": "hall", "channel": "siren", "state": True} in Recorder.calls)


def test_threshold_fires_once_when_crossing(home):
    for v in (50, 25, 20, 18, 45, 22):
        home.events.log("sensor", node="garden", input="soil", value=v)
    assert wait_for(lambda: len(Recorder.calls) >= 2)
    time.sleep(0.15)
    assert Recorder.calls.count({"node": "garden", "channel": "pump", "state": True}) == 2  # 25 and 22


def test_automation_for_hold(home):
    class FakeSensors:
        values = {}
        offline = set()

        def current(self, node, inp):
            return {"state": self.state}
    home.sensors = FakeSensors()
    home.sensors.state = "off"
    home.events.log("sensor", node="front door", input="contact", state="off")
    time.sleep(0.12)
    home.timeline.run_due()
    assert wait_for(lambda: "door_left_open" in keys(home))
    # closes again before the hold time: no announcement
    home.said.clear()
    home.events.log("sensor", node="front door", input="contact", state="off")
    home.sensors.state = "on"
    time.sleep(0.12)
    home.timeline.run_due()
    time.sleep(0.1)
    assert "door_left_open" not in keys(home)


def test_conditional_routine_step(home):
    home.run_action({"backend": "routine", "routine": "smart"})
    assert home.said[-1][0] == "Welcome back"
    home.state.set_mode("away")
    home.run_action({"backend": "routine", "routine": "smart"})
    assert {"node": "all", "channel": "lights", "state": False} in Recorder.calls


def test_vacation_lights(home, tmp_path):
    home.config.vacation = {"lights": [{"backend": "dev", "node": "kitchen", "channel": "lights"}],
                            "from": "00:00", "until": "23:59"}
    home.state.set_mode("vacation")
    import random

    random.seed(3)
    for _ in range(6):
        home.scheduler._next_vacation = 0
        home.scheduler.vacation_tick(clock.now().replace(hour=20))
    assert any(c.get("node") == "kitchen" for c in Recorder.calls)


# ---------------------------------------------------------------- modes, presence, doors
def test_modes_and_presence_events(home):
    home.state.set_presence("sam", "home")
    home.state.set_presence("mia", "home")
    home.state.set_presence("sam", "away")
    home.state.set_presence("mia", "away")
    kinds = [e["kind"] for e in home.events.query("presence")]
    assert kinds == ["presence.arrived", "presence.first_home", "presence.arrived", "presence.left",
                     "presence.left", "presence.everyone_left"]
    home.run_action({"backend": "presence", "presence": "home"}, {"here": "bedroom"})
    assert home.state.home_people() == ["sam"]


def test_door_scan_marks_person_home(home):
    from phrasehome.presence import Presence

    Presence(home)
    home.events.log("scan", result="allowed", name="Samuel", door="front")
    assert wait_for(lambda: home.state.home_people() == ["sam"])


def test_auto_relock_waits_for_door_to_close(home):
    class FakeSensors:
        state = "off"  # contact open
        offline = set()

        def current(self, node, inp):
            return {"state": self.state}
    home.sensors = FakeSensors()
    home.doors._remember("front", "locked")
    home.doors.set("front", "unlocked")
    time.sleep(0.2)
    home.timeline.run_due()
    assert home.doors.state("front") == "unlocked"     # still open: wait
    home.sensors.state = "on"                           # closed
    home.timeline._heap = [(0, 0, f) for _, _, f in home.timeline._heap]  # skip the 60 s wait
    home.timeline.run_due()
    assert home.doors.state("front") == "locked"
    assert "door_auto_locked" in keys(home)


# ---------------------------------------------------------------- fingerprint
def test_fingerprint_needs_matching_face(home):
    fp = FingerprintDoor(home, home.config.fingerprint[0])
    fp.led = lambda *a: None
    fp.bank.enroll("Samuel", [__import__("numpy").ones(4, dtype="float32") / 2])
    fp.bank.add_fingerprint("Samuel", 7)
    home.doors._remember("front", "locked")
    assert fp.on_finger(True, 7) == "rejected"          # no face scan first
    home.events.log("scan.awaiting_fingerprint", door="front", person="samuel", name="Samuel")
    assert fp.on_finger(True, 7) == "accepted"
    assert home.doors.state("front") == "unlocked"
    assert "fingerprint_accepted" in keys(home)


def test_fingerprint_lockout(home):
    fp = FingerprintDoor(home, home.config.fingerprint[0])
    fp.led = lambda *a: None
    home.doors._remember("front", "unlocked")
    assert [fp.on_finger(False, None) for _ in range(3)] == ["rejected", "rejected", "lockout"]
    assert home.doors.state("front") == "locked"
    assert "fingerprint_lockout" in keys(home)


# ---------------------------------------------------------------- sensors
def test_sensor_watcher_events_and_offline(home):
    w = SensorWatcher(home)
    st = lambda **kw: {"inputs": [{"name": "contact", "on": kw.get("on", True)},
                                  {"name": "bell", "on": False, "count": kw.get("count", 0)},
                                  {"name": "soil", "value": kw.get("soil", 50)}]}
    w.poll_node("porch", "x", st())
    w.poll_node("porch", "x", st(on=False, count=2, soil=51))
    w.poll_node("porch", "x", st(on=False, count=2, soil=40))
    evs = [(e.get("input"), e.get("state"), e.get("value")) for e in home.events.query("sensor")]
    assert evs == [("contact", "off", None), ("bell", "pressed", None), ("soil", None, 40.0)]
    assert w.current("porch", "contact")["state"] == "off"
    w._failures.clear()
    for _ in range(3):
        w.poll_node("dead", "127.0.0.1:1")
    assert "dead" in w.offline and home.events.query("device.offline")


# ---------------------------------------------------------------- doorbell
def test_doorbell(home):
    from phrasehome.door_scanner import DoorScanner
    from tests.test_door import FakeEngine

    sc = DoorScanner(home, {"door": "front"}, engine=FakeEngine())
    sc.doorbell_pressed(None)
    assert home.events.query("doorbell")
    assert wait_for(lambda: "doorbell_announce" in keys(home))
    assert any(x[0] == "doorbell" and x[3] for x in home.said)  # chime everywhere


# ---------------------------------------------------------------- voice routing
def test_broadcast_skips_dnd_and_quiet_rooms(tmp_path):
    (tmp_path / "c.yaml").write_text(HOUSE.replace('quiet_hours: "00:00-00:01"', 'quiet_hours: "00:00-23:59"'))
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    try:
        v = h.voice
        h.instances.set_dnd(h.instances.all["kid"], True)
        assert v.targets(None, ["kitchen", "bedroom", "mia's room"], False, False) == ["kitchen"]
        assert v.targets(None, ["bedroom", "mia's room"], False, True) == ["bedroom", "mia's room"]
        assert v.settings_for("bedroom")["volume"] == 70 and v.settings_for("kitchen")["volume"] == 100
    finally:
        h.close()


def test_audio_router_prefers_room_satellite(home, monkeypatch):
    from phrasehome import audio_out

    sent = []

    class Sats:
        modules = {}

        def has_speaker(self, room):
            return room == "bedroom"

        def speaker_rooms(self):
            return ["bedroom"]

        def send_voice(self, room, pcm):
            sent.append((room, pcm))

    home.satellites = Sats()
    monkeypatch.setattr(audio_out, "convert", lambda a, rate, ch, vol: f"{rate}/{ch}/{vol:.1f}".encode())
    monkeypatch.setattr(audio_out.shutil, "which", lambda x: "/usr/bin/" + x)
    r = audio_out.AudioRouter(home)
    r._play_local = lambda *a: sent.append(("local",))
    assert r.play(home.config.base_dir / "x.wav", "bedroom", 0.7) == "satellite"
    assert sent == [("bedroom", b"16000/1/0.7")]
    assert r.play(home.config.base_dir / "x.wav", "garage", 1.0) == "local"
    assert "bedroom" in r.rooms() and "kitchen" in r.rooms()


def test_satellite_hello_voice_and_echo_mute():
    from phrasehome.satellites import SatelliteServer

    got = []
    srv = SatelliteServer(lambda *a: got.append(a), host="127.0.0.1", port=0)
    srv.start()
    c = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    c.settimeout(2)
    c.sendto(b"PHC1" + bytes([1, 7]) + b"bedroom", ("127.0.0.1", srv.port))
    assert wait_for(lambda: srv.has_speaker("bedroom"))
    srv.send_voice("bedroom", b"\x01\x00" * 1500)  # ~94 ms of speech -> 3 packets
    pkts = [c.recv(2048) for _ in range(3)]
    assert all(p[:4] == b"PHV1" and len(p) == 8 + 1024 for p in pkts)
    assert srv.is_muted("bedroom")                  # its own mic is ignored while it talks
    c.sendto(b"PHM1" + bytes([7]) + b"bedroom" + b"\0\0" * 8, ("127.0.0.1", srv.port))
    time.sleep(0.1)
    assert got == []
    srv.close()


def test_speaker_announce_to_one_room():
    from phrasehome.speakers import BYTES_PER_PACKET, SpeakerHub

    hub = SpeakerHub("m", {"speakers": {"kitchen": "k", "bedroom": "b"}})
    hub.members = {"kitchen": "10.0.0.1", "bedroom": "10.0.0.2"}
    assert hub.announce(b"\1" * 10, names=["bedroom"]) == ["bedroom"]
    assert hub._next_announcement("10.0.0.1") is None
    assert len(hub._next_announcement("10.0.0.2")) == BYTES_PER_PACKET


# ---------------------------------------------------------------- assistant with instances
def test_assistant_main_room_manages_everyone(home):
    sam = home.instances.all["sam"]
    client = FakeClient(
        [tool("agenda", action="add", kind="reminder", text="feed the cat", when="", in_minutes=30,
              repeat="none", person="Sam"),
         tool("lists", action="add", list="shopping", items=["cat food"], person="")],
        [text("Done.")])
    ai = Assistant(home, client=client)
    ai.ask("remind Sam to feed the cat in half an hour and add cat food to the list", room="living room")
    (item,) = home.instances.items(sam)
    assert item["text"] == "feed the cat" and item["room"] == "bedroom"
    assert home.instances.list_get(sam, "shopping") == ["cat food"]
    assert "main room" in client.requests[0]["system"][0]["text"]


def test_assistant_personal_room_is_private(home):
    client = FakeClient(
        [tool("agenda", action="list", kind="any", text="", when="", in_minutes=0, repeat="none",
              person="Mia")],
        [text("I can't see Mia's reminders.")])
    Assistant(home, client=client).ask("what are Mia's reminders?", room="bedroom")
    res = client.requests[1]["messages"][-1]["content"][0]
    assert res["is_error"] and "main room" in res["content"]


def test_assistant_kid_mode_and_wake_names(home):
    ai = Assistant(home, client=FakeClient())
    _, tools = ai.profile(home.instances.all["kid"])
    names = {t["name"] for t in tools}
    assert not names & {"set_door", "run_routine", "set_mode", "schedule_command"}
    cmd_enum = next(t for t in tools if t["name"] == "run_command")["input_schema"]["properties"]["command"]["enum"]
    assert "front_lock" not in cmd_enum and "lights" in cmd_enum
    assert ai.addressed("jarvis what time is it", "bedroom") == "what time is it"
    assert ai.addressed("jarvis what time is it", "kitchen") is None   # Sam's wake name only in Sam's room
    assert ai.addressed("house hello", "bedroom") == "hello"
    assert "jarvis" in ai.vocabulary()


def test_assistant_intercom_and_schedule(home):
    client = FakeClient(
        [tool("announce", text="Dinner's ready!", rooms=[], person="Sam", everyone=False),
         tool("schedule_command", command="lights", slots=[], when="", in_minutes=20)],
        [text("Told Sam, and the lights go on in 20 minutes.")])
    Assistant(home, client=client).ask("tell Sam dinner's ready and turn on the lights in 20 minutes",
                                       room="kitchen")
    assert ("intercom", None, ["bedroom"]) == home.said[0][:3]
    (act,) = home.instances.items(home.instances.main, ("action",))
    assert act["action"]["node"] == "kitchen"


# ---------------------------------------------------------------- dashboard
def test_dashboard_api(home):
    home.config.web = {"port": 0, "host": "127.0.0.1", "token": "t0k"}
    web = WebServer(home)
    web.start()
    base = f"http://127.0.0.1:{web.port}"

    def call(path, body=None, token="t0k"):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"X-Token": token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert call("/api/status", token="nope")[0] == 401
        code, st = call("/api/status")
        assert code == 200 and st["mode"] == "home" and {i["id"] for i in st["instances"]} == {"household", "sam", "kid"}
        assert call("/api/list", {"list": "shopping", "add": ["apples"]})[1]["list"] == ["apples"]
        assert call("/api/door", {"door": "front", "state": "unlocked"})[0] == 403
        assert call("/api/mode", {"mode": "night"})[0] == 200 and home.state.mode == "night"
        assert call("/api/event", {"kind": "garage_opened", "by": "remote"})[0] == 200
        assert home.events.query("external.garage_opened")
        with urllib.request.urlopen(base + "/", timeout=3) as r:
            assert b"<title>Home</title>" in r.read()
    finally:
        web.stop()


def test_ntfy_backend_with_attachment(tmp_path):
    from phrasehome.actions.ntfy import NtfyBackend

    got = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self):
            got.append((self.command, self.path, dict(self.headers), self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        do_POST = do_PUT = _ok

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    photo = tmp_path / "scan.jpg"
    photo.write_bytes(b"JPEG")
    be = NtfyBackend("phone", {"server": f"http://127.0.0.1:{srv.server_port}", "topic": "house"}, None)
    be.run({"message": "Someone's at the door", "title": "Door", "priority": 4}, "")
    be.run({"message": "Unknown visitor", "attach": str(photo)}, "")
    srv.shutdown()
    assert got[0][:2] == ("POST", "/house") and got[0][3] == b"Someone's at the door" and got[0][2]["Title"] == "Door"
    assert got[1][0] == "PUT" and got[1][3] == b"JPEG" and got[1][2]["Filename"] == "scan.jpg"


def test_calendar_simple_parser():
    from phrasehome.calendar_feed import events

    ics = (b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261125T170000Z\nDTEND:20261125T180000Z\n"
           b"SUMMARY:Dentist\nEND:VEVENT\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20261201\nSUMMARY:Later\n"
           b"END:VEVENT\nEND:VCALENDAR\n")
    start = datetime(2026, 11, 25).astimezone()
    evs = events("", start, start + timedelta(days=2), ics=ics)
    assert [e["summary"] for e in evs] == ["Dentist"]


def test_generated_sounds(tmp_path):
    import wave

    from phrasehome.sounds import make

    f = make("doorbell", tmp_path)
    with wave.open(str(f)) as w:
        assert w.getframerate() == 22050 and w.getnframes() > 20000
    assert make("not a sound", tmp_path) is None


def test_privacy(home):
    home.run_action({"backend": "privacy", "privacy": "on", "minutes": 1}, {"here": "kitchen"})
    assert home.state.is_private("bedroom") and keys(home) == ["privacy_on"]
    home.run_action({"backend": "privacy", "privacy": "off"}, {"here": "kitchen"})
    assert not home.state.is_private("bedroom")
    home.run_action({"backend": "privacy", "privacy": "on", "room": "here"}, {"here": "bedroom"})
    assert home.state.is_private("bedroom") and not home.state.is_private("kitchen")
