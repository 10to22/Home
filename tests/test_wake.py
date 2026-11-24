"""Morning alarms that make sure you're up, the alarms page, and the mean voice."""

import re
import time

import pytest

from phrasehome import clock
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.voice import DEFAULT_PHRASES, STYLES, Voice
from tests.test_door import Recorder  # noqa: F401  (registers the "record" backend)

CONFIG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none, style: plain }
instances:
  household: { name: Household, main: true, rooms: [kitchen, hallway] }
  sam: { name: Sam, rooms: [bedroom] }
  mia: { name: Mia, rooms: [mia's room] }
beds:
  sam: { node: bedroom, input: bed }
rooms:
  occupancy:
    hallway: { node: hallway, input: motion }
    bedroom: { node: bedroom, input: presence }
wake:
  up_seconds: 30
  escalate: [ { node: "{here}", channel: lights, state: on } ]
  on_up: [ { node: kitchen, channel: coffee, state: on } ]
commands:
  - phrases: [stop, stop the alarm]
    action: { alarm: stop }
  - phrases: [snooze]
    action: { alarm: snooze }
"""


class Beds:
    """Stands in for the room tracker: who's in bed."""

    def __init__(self, home):
        self.home, self.beds, self.in_bed = home, {"sam": {"node": "bedroom", "input": "bed"}}, {}

    def set(self, person, in_bed):
        if in_bed:
            self.in_bed[person] = time.time()
        else:
            self.in_bed.pop(person, None)
        self.home.events.log("bed.in" if in_bed else "bed.out", person=person)


class Clock:
    def __init__(self, monkeypatch):
        self.offset = 0.0
        from datetime import timedelta

        real_mono, real_time, real_now = time.monotonic, time.time, clock.now
        monkeypatch.setattr(time, "monotonic", lambda: real_mono() + self.offset)
        monkeypatch.setattr(time, "time", lambda: real_time() + self.offset)
        monkeypatch.setattr(clock, "now", lambda: real_now() + timedelta(seconds=self.offset))

    def advance(self, home, seconds):
        self.offset += seconds
        home.timeline.run_due()


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "c.yaml").write_text(CONFIG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.voice.dry_run = True  # no speaker here: just record what it would say
    h.spoken = []
    real = h.voice.speak
    h.voice.speak = lambda key, **v: h.spoken.append(real(key, **v)) or h.spoken[-1]
    h.rooms = Beds(h)
    h.clock = Clock(monkeypatch)
    yield h
    h.close()


def ring(home, who="sam", wake=None, room=None):
    inst = home.instances.all[who]
    item = home.instances.add(inst, "alarm", clock.now(), label="work", room=room)
    if wake is not None:
        home.instances.update_item(inst, item["id"], wake=wake)
    home.scheduler.tick()
    return home.wake.session_for(room or inst.rooms[0])


def say(home, text, room="bedroom"):
    """As if `text` ("stop" / "snooze") was said in that room."""
    return home.run_action({"backend": "alarm", "alarm": text}, {"here": room})


def wait_for(fn, t=2.0):
    end = time.monotonic() + t
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.02)
    return fn()


def test_stop_in_bed_only_quiets_it_then_it_nags_until_youre_up(home):
    home.rooms.set("sam", True)
    s = ring(home)
    assert s.state == "ringing" and "bedroom" in home.scheduler.ringers
    say(home, "stop")
    assert s.state == "quiet" and "bedroom" not in home.scheduler.ringers
    assert home.spoken[-1] == "You're still in bed. I'll ring again in 3 minutes."
    home.clock.advance(home, 181)  # still in bed: it rings again
    assert s.state == "ringing" and s.nags == 1 and "bedroom" in home.scheduler.ringers
    assert "This is your first reminder." in " ".join(home.spoken)
    say(home, "stop")
    home.clock.advance(home, 181)  # second nag: the lights come on
    assert s.nags == 2
    assert wait_for(lambda: {"node": "bedroom", "channel": "lights", "state": True} in Recorder.calls)
    home.rooms.set("sam", False)  # out of bed...
    home.clock.advance(home, 10)
    assert s.state == "ringing"  # ...not for long enough yet
    home.clock.advance(home, 25)
    assert s.state == "up" and "bedroom" not in home.scheduler.ringers
    (ev,) = home.events.query("wake.up")
    assert (ev["person"], ev["nags"], ev["minutes"]) == ("sam", 2, 7)  # 6.6 minutes
    assert wait_for(lambda: {"node": "kitchen", "channel": "coffee", "state": True} in Recorder.calls)


def test_quick_trip_out_of_bed_doesnt_count(home):
    home.rooms.set("sam", True)
    s = ring(home)
    home.rooms.set("sam", False)
    home.clock.advance(home, 10)
    home.rooms.set("sam", True)  # straight back in
    home.clock.advance(home, 60)
    assert s.state == "ringing"


def test_back_to_bed_rings_again(home):
    home.rooms.set("sam", True)
    s = ring(home)
    home.rooms.set("sam", False)
    home.clock.advance(home, 31)
    assert s.state == "up"
    home.clock.advance(home, 120)
    home.rooms.set("sam", True)  # sneaking back in 2 minutes later
    assert s.state == "ringing" and "bedroom" in home.scheduler.ringers
    assert "Back in bed? The alarm's back on." in home.spoken[-3:]


def test_snooze_limit_and_counts_carry_over(home):
    home.rooms.set("sam", True)
    s = ring(home)
    say(home, "snooze")
    assert home.spoken[-1] == "Snoozing for 9 minutes. 1 snoozes left."
    first = s.first_rang
    home.clock.offset += 9 * 60 + 1  # the snoozed alarm goes off
    home.scheduler.tick()
    s2 = home.wake.session_for("bedroom")
    assert s2 is not s and s2.snoozes == 1 and s2.first_rang == first
    say(home, "snooze")
    home.clock.offset += 9 * 60 + 1
    home.scheduler.tick()
    s3 = home.wake.session_for("bedroom")
    assert s3.snoozes == 2
    say(home, "snooze")  # the third: refused, keeps ringing
    assert home.spoken[-1] == "No more snoozes today. Time to get up."
    assert "bedroom" in home.scheduler.ringers and s3.state == "ringing"


def test_already_up_means_stop_just_stops(home):
    s = ring(home)  # Sam isn't in bed when it goes off
    say(home, "stop")
    assert s.state == "up" and "bedroom" not in home.scheduler.ringers


def test_without_a_bed_sensor_motion_elsewhere_counts(home):
    s = ring(home, "mia")
    assert s is not None and not s.has_bed
    say(home, "stop", room="mia's room")
    assert s.state == "quiet"
    home.events.log("sensor", node="hallway", input="motion", state="on")
    assert s.state in ("up", "done") and home.events.query("wake.up")
    assert "mia's room" not in home.scheduler.ringers


def test_gives_up_and_tells_the_phone(home, monkeypatch):
    sent = []
    monkeypatch.setattr(home, "notify_person", lambda inst, msg, title="": sent.append(msg))
    home.rooms.set("sam", True)
    s = ring(home)
    say(home, "stop")
    home.clock.advance(home, 45 * 60 + 1)
    assert s.state == "done" and home.events.query("wake.gave_up")
    assert sent and "Sam still isn't up" in sent[0]


def test_plain_alarm_when_make_sure_up_is_off(home):
    home.rooms.set("sam", True)
    assert ring(home, wake={"make_sure_up": False}) is None
    say(home, "stop")
    assert "bedroom" not in home.scheduler.ringers


def test_alarm_says_the_time(home):
    home.rooms.set("sam", True)
    ring(home)
    assert any(t.startswith("It's ") and "{time}" not in t for t in home.spoken)


# ---------- the alarms page ----------
def test_alarms_page_api(home):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = home
    r = web.alarms_post("save", {"instance": "sam", "time": "06:45", "repeat": "weekdays", "label": "gym",
                                 "wake": {"make_sure_up": True, "snooze_limit": 1, "recheck_minutes": 2}})
    aid = r["item"]["id"]
    (a,) = web.alarms_get()["alarms"]
    assert (a["time"], a["repeat"], a["label"], a["sensor"]) == ("06:45", "weekdays", "gym", "bed")
    assert a["wake"]["snooze_limit"] == 1 and a["wake"]["recheck_minutes"] == 2 and a["wake"]["up_seconds"] == 30
    web.alarms_post("save", {"instance": "sam", "id": aid, "time": "07:10", "repeat": "mon,wed,fri"})
    (a,) = web.alarms_get()["alarms"]
    assert a["time"] == "07:10" and a["repeat"] == "mon,wed,fri" and a["wake"]["snooze_limit"] == 1
    assert clock.day_matches("mon,wed,fri", clock.datetime.fromisoformat(a["due"]).date())
    web.alarms_post("save", {"instance": "sam", "id": aid, "off": True})  # paused: never due
    home.clock.offset += 8 * 86400
    assert not home.instances.due(clock.now())
    web.alarms_post("save", {"instance": "sam", "id": aid, "off": False})  # back on: next one
    assert clock.datetime.fromisoformat(web.alarms_get()["alarms"][0]["due"]) > clock.now()
    web.alarms_post("ring", {"instance": "sam", "id": aid})  # test it now
    home.rooms.set("sam", True)
    home.scheduler.tick()
    s = home.wake.session_for("bedroom")
    assert s is not None and s.cfg["snooze_limit"] == 1
    assert web.alarms_get()["sessions"][0]["state"] == "ringing"
    web.alarms_post("stop", {"room": "bedroom"})
    assert s.state == "quiet"
    web.alarms_post("delete", {"instance": "sam", "id": aid})
    assert len(web.alarms_get()["alarms"]) == 0


def test_limits_are_clamped(home):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = home
    r = web.alarms_post("save", {"instance": "sam", "time": "07:00", "wake": {"snooze_limit": 99,
                                                                             "recheck_minutes": -5}})
    assert r["item"]["wake"] == {"snooze_limit": 10, "recheck_minutes": 0.5}


# ---------- mean by default ----------
def test_mean_is_the_default_and_plain_turns_it_off(home):
    home.config.voice = {"engine": "none"}
    assert Voice(home.config, dry_run=True).phrases["door_auto_locked"] == STYLES["mean"]["door_auto_locked"]
    home.config.voice = {"engine": "none", "style": "plain"}
    assert Voice(home.config, dry_run=True).phrases["door_auto_locked"] == DEFAULT_PHRASES["door_auto_locked"]


def test_safety_lines_stay_plain_in_every_style():
    for style in STYLES.values():
        for key in ("smoke_alarm", "co_alarm", "leak_alarm", "weather_alert", "weather_warning", "panic"):
            assert key not in style


EXTRA = {"alarm": {"time"}, "wake_up": {"snoozes", "nags"}, "wake_snoozed": {"left"},
         "wake_snooze_denied": {"snoozes"},
         "wake_nag": {"count"}, "good_morning": set(), "all_clear": set()}


@pytest.mark.parametrize("style", sorted(STYLES))
def test_style_lines_only_use_values_the_house_passes(style):
    def fields(v):
        return set(re.findall(r"{(\w+)}", " ".join(v if isinstance(v, list) else [str(v)])))

    for key, lines in STYLES[style].items():
        allowed = fields(DEFAULT_PHRASES.get(key, "")) | EXTRA.get(key, set()) | {"name"}
        assert fields(lines) <= allowed, (key, fields(lines) - allowed)


def test_mean_persona_is_the_assistant_default():
    from phrasehome.assistant import PERSONAS, Assistant

    class C:
        assistant = {}

    a = Assistant.__new__(Assistant)
    cfg = C.assistant
    a.persona = PERSONAS.get(cfg.get("persona", "mean"), cfg.get("persona") or PERSONAS["mean"])
    assert a.persona == PERSONAS["mean"] and "safety" in a.persona
