"""The student-house features (house_life.py): people, party, study, exams, door status, meetings,
the shower timer, last one out, windows, the winter lamp, pollen, walk home, the quiz and the radio."""

import time
from datetime import datetime, timedelta

import pytest

from phrasehome import clock
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from tests.test_door import Recorder  # noqa: F401  (registers the record backend)
from tests.test_wake import Clock

CONFIG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none, style: aussie_uncut }
location: { latitude: 51.5, longitude: -0.1 }
instances:
  household: { name: Household, main: true, rooms: [living room, kitchen] }
  finn: { name: Finn, rooms: [finn's room], phone: finn-topic }
  parker: { name: Parker, rooms: [parker's room], phone: parker-topic, about: [studies law] }
  alex: { name: Alex, rooms: [alex's room], phone: alex-topic }
  jo: { name: Jo, rooms: [jo's room], phone: jo-topic }
doors:
  front:
    lock: [ { node: front, channel: lock, state: on } ]
    unlock: [ { node: front, channel: lock, state: off } ]
routines:
  party:
    - { node: living, channel: disco, state: on }
party:
  guest_wifi: { ssid: Party Time, password: letsgo123 }
  quiet_time: "23:00"
  cleanup: [bottles, kitchen, vacuum, bins]
study: { focus: 25, break: 5, long_break: 15, long_every: 2 }
door_status:
  rooms:
    finn's room: { node: finn_door }
shower:
  sensor: { node: bathroom, input: humidity }
  rise: 8
windows:
  lounge window: { node: lounge, input: window }
winter_lamp:
  lights:
    finn's room: { node: finn, channel: lamp }
  months: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
  from: "00:00"
  until: "23:59"
walk_home: { minutes: 20, grace: 10, again: 20, places: { library: 15 } }
commands:
  - { name: lockdown, phrases: [lockdown], action: { routine: party } }
  - { name: my_party, phrases: [party mode], action: { routine: party } }
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "c.yaml").write_text(CONFIG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.voice.dry_run = True
    h.said, h.sent = [], []
    real = h.voice.speak

    def speak(key, **v):
        out = real(key, **v)
        h.said.append((key, out))
        return out

    h.voice.speak = speak
    monkeypatch.setattr(h, "notify_person", lambda inst, msg, title="", **kw: h.sent.append((inst.id, title, msg)))
    h.life.sync = True
    h.clock = Clock(monkeypatch)
    yield h
    h.close()


def keys(home):
    return [k for k, _ in home.said]


def life(home):
    return home.life


# ---------------------------------------------------------------- people (72)
def test_parker_is_a_vegetarian(home):
    L = life(home)
    assert L.quick("parker's a vegetarian", room="kitchen")
    parker = home.instances.all["parker"]
    assert "is vegetarian" in L.people.facts(parker)
    assert home.instances.diet(parker)["style"] == "vegetarian"  # the meal planner reads this
    assert keys(home)[-1] == "people_saved_diet" and "Parker" in home.said[-1][1]
    assert "Parker: studies law; is vegetarian" in L.context_line()  # every AI question sees it


def test_meal_planner_respects_it(home):
    from phrasehome.meals import Meals

    life(home).quick("parker is vegan", room="kitchen")
    life(home).quick("remember jo's allergic to peanuts", room="kitchen")
    diets = Meals(home).diets()
    assert diets["Parker"]["style"] == "vegan" and diets["Jo"]["allergies"] == ["peanuts"]


def test_other_facts_questions_and_forgetting(home):
    L = life(home)
    assert L.quick("finn hates coriander", room="kitchen")
    assert L.quick("i'm allergic to shellfish", room="alex's room")  # from your own room: you
    assert "is allergic to shellfish" in L.people.facts(home.instances.all["alex"])
    assert L.quick("what do you know about finn", room="kitchen")
    assert keys(home)[-1] == "people_about" and "coriander" in home.said[-1][1]
    L.quick("parker's vegetarian", room="kitchen")
    assert L.quick("parker isn't vegetarian anymore", room="kitchen")
    assert "vegetarian" not in str(home.instances.diet(home.instances.all["parker"]).get("style", ""))
    assert not any("vegetarian" in f for f in L.people.facts(home.instances.all["parker"]))


def test_not_every_sentence_about_someone_is_a_fact(home):
    L = life(home)
    for said in ("finn's room is a mess", "parker is home", "jo is going to the shops", "is finn home",
                 "alex is cooking tonight", "parker isn't home", "i'm tired"):
        assert not L.quick(said, room="alex's room"), said
    assert not L.quick("finn hates coriander", room="finn's room", strict=True)  # no name said: not saved
    assert all(not L.people.facts(i) or i.id == "parker" for i in L.people.housemates())


def test_the_ai_gets_the_tool_and_the_context(home):
    from phrasehome.assistant import Assistant
    from tests.test_assistant import FakeClient, text, tool

    life(home).quick("parker's vegetarian", room="kitchen")
    client = FakeClient([tool("housemates", action="remember", person="Jo", fact="hates mushrooms")],
                        [text("Noted.")])
    ai = Assistant(home, client=client)
    ai.ask("jo hates mushrooms, remember that", room="kitchen")
    req = client.requests[0]
    assert {"housemates", "exams", "walk_home", "house_meeting"} <= {t["name"] for t in req["tools"]}
    assert "Parker: studies law; is vegetarian" in str(req["messages"])
    assert "hates mushrooms" in life(home).people.facts(home.instances.all["jo"])


# ---------------------------------------------------------------- party (7)
def test_party_planner(home):
    home.run_action({"backend": "life", "life": "party_start"}, {"here": "living room"})
    p = life(home).party
    assert p.active and "Party Time" in home.said[-1][1] and "letsgo123" in home.said[-1][1]
    p.active["quiet_at"] = time.time() + 31 * 60
    p.tick()
    assert "party_quiet_soon" not in keys(home)  # the 60 minute warning is long gone
    home.clock.advance(home, 2 * 60)
    p.tick()
    assert home.said[-1][0] == "party_quiet_soon" and "30" in home.said[-1][1]
    home.clock.advance(home, 30 * 60)
    p.tick()
    assert "party_quiet_now" in keys(home)
    home.sent.clear()
    home.run_action({"backend": "life", "life": "party_stop"}, {"here": "living room"})
    assert sorted(s[0] for s in home.sent) == ["alex", "finn", "jo", "parker"]  # one cleanup job each
    jobs = [i["text"] for inst in home.instances.all.values() for i in home.instances.items(inst, ("reminder",))]
    assert sorted(jobs) == ["Party cleanup: bins", "Party cleanup: bottles", "Party cleanup: kitchen",
                            "Party cleanup: vacuum"]


def test_your_own_party_command_gets_the_planner_too(home):
    # "party mode" is the config's own command (runs the routine): the built-in one steps aside
    names = [c.name for c in home.config.commands if "party mode" in c.phrases]
    assert names == ["my_party"]
    home.run_action({"routine": "party", "backend": "routine"})
    assert life(home).party.active is not None
    assert {"node": "living", "channel": "disco", "state": True} in Recorder.calls


def test_exam_tomorrow_brings_quiet_time_forward(home):
    parker = home.instances.all["parker"]
    tomorrow = (clock.now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    life(home).exams.add(parker, "contract law", tomorrow)
    at, why = life(home).party.quiet_time(clock.now().replace(hour=19, minute=0))
    assert at.strftime("%H:%M") == "21:30" and "Parker has an exam tomorrow" in why


# ---------------------------------------------------------------- study (18), exams (8), door status (11)
def test_study_mode_blocks_breaks_and_summary(home):
    home.run_action({"backend": "life", "life": "study_on"}, {"here": "finn's room"})
    s = life(home).study
    assert s.studying("finn's room") and home.instances.dnd(home.instances.all["finn"])
    home.clock.advance(home, 25 * 60 + 1)
    s.tick()
    assert keys(home)[-1] == "study_break"
    home.clock.advance(home, 5 * 60 + 1)
    s.tick()
    assert keys(home)[-1] == "study_back"
    home.clock.advance(home, 25 * 60 + 1)
    s.tick()
    assert keys(home)[-1] == "study_long_break"  # every second block here
    home.run_action({"backend": "life", "life": "study_off"}, {"here": "finn's room"})
    assert "2 focus blocks, 50 minutes" in home.said[-1][1]
    assert not home.instances.dnd(home.instances.all["finn"])
    assert s.week() == {"finn": 50}


def test_door_light_follows_status(home):
    L = life(home)
    L.door_status.refresh()
    assert {"node": "finn_door", "channel": "green", "state": True} in Recorder.calls
    home.run_action({"backend": "life", "life": "study_on"}, {"here": "finn's room"})
    Recorder.calls.clear()
    L.door_status.refresh()
    lit = {c["channel"] for c in Recorder.calls if c["state"]}
    assert lit == {"red", "green"}  # amber: studying
    assert L.quick("set my door to busy", room="finn's room")
    assert L.door_status.status("finn's room") == "busy"
    assert L.quick("is finn busy", room="kitchen") and "busy" in home.said[-1][1]


def test_freak_mode_shows_busy_without_saying_why(home):
    home.freak.start("finn's room")
    assert life(home).door_status.status("finn's room") == "busy"


def test_exams_said_out_loud_and_the_night_before(home):
    L = life(home)
    assert L.quick("i've got a calc exam tomorrow at 9", room="parker's room")
    ex = L.exams.all()
    assert ex[0][0].id == "parker" and ex[0][1]["subject"] == "calc" and ex[0][1]["at"].hour == 9
    evening = clock.now().replace(hour=19, minute=5)
    home.sent.clear()
    L.exams.tick(evening)
    assert sorted(s[0] for s in home.sent) == ["alex", "finn", "jo"]  # not Parker
    assert "Parker has an exam tomorrow at 9" in home.sent[0][2]
    assert not L.exams.quiet_now(evening)
    assert L.exams.quiet_now(clock.now().replace(hour=22, minute=0))


def test_parse_when():
    from phrasehome.study import parse_when

    now = datetime(2026, 11, 26, 12, 0).astimezone()  # a Thursday
    assert parse_when(" on friday at 2pm", now).strftime("%a %H:%M") == "Fri 14:00"
    assert parse_when(" tomorrow at 9", now).strftime("%d %H:%M") == "27 09:00"
    assert parse_when(" on monday at two thirty", now).strftime("%a %H:%M") == "Mon 14:30"
    assert parse_when(" soon", now) is None


# ---------------------------------------------------------------- house meeting (9)
def test_house_meeting_books_a_free_time_and_reads_the_agenda(home, monkeypatch):
    L = life(home)
    m = L.meeting
    now = clock.now()
    blocked = (now + timedelta(days=1)).replace(hour=0, minute=0)
    monkeypatch.setattr(m, "busy", lambda now=None: [(blocked, blocked + timedelta(days=1), "jo")])
    assert L.quick("add the bins rota to the meeting agenda", room="kitchen")
    home.run_action({"backend": "life", "life": "meeting_book"}, {"here": "kitchen"})
    b = m.booked()
    at = datetime.fromisoformat(b["at"])
    assert at.date() != blocked.date() and 18 <= at.hour < 22
    assert sorted(s[0] for s in home.sent) == ["alex", "finn", "jo", "parker"]
    assert "bins rota" in home.sent[0][2]
    m.tick(at + timedelta(minutes=1))
    assert keys(home)[-1] == "meeting_now" and "bins rota" in home.said[-1][1]


def test_meeting_avoids_when_people_are_usually_out(home):
    m = life(home).meeting
    rate = m.home_rate(0, 19, evs=[])
    assert rate == 0.5  # no history yet: every evening is as good as another


# ---------------------------------------------------------------- shower timer (34)
def test_shower_timer_from_humidity(home):
    s = home.sensors
    if s is None:
        from phrasehome.sensors import SensorWatcher

        s = SensorWatcher(home)
    w = life(home).watch
    home.events.log("sensor", node="bathroom", input="humidity", value=55.0)
    home.events.log("sensor", node="bathroom", input="humidity", value=70.0)
    assert w.shower is not None
    home.clock.advance(home, 10 * 60 + 5)
    w.tick()
    assert keys(home)[-1] == "shower_warn"
    home.clock.advance(home, 20 * 60)
    w.tick()
    assert keys(home)[-1] == "shower_roast" and "30" in home.said[-1][1]
    home.events.log("sensor", node="bathroom", input="humidity", value=58.0)
    assert w.shower is None and w.shower_record() == ("", 30)


# ---------------------------------------------------------------- last one out (44), windows (45)
def test_last_one_out_gets_named_and_the_door_locked(home):
    home.doors.set("front", "unlocked")
    home.sent.clear()
    home.state.set_presence("finn", "home")
    home.state.set_presence("finn", "away")  # the last one out
    assert home.doors.state("front") == "locked"
    to_finn = [s for s in home.sent if s[0] == "finn"]
    others = [s for s in home.sent if s[0] != "finn"]
    assert to_finn and "front door" in to_finn[0][2]
    assert sorted(s[0] for s in others) == ["alex", "jo", "parker"] and all("Finn" in s[2] for s in others)
    assert life(home).watch.last_out_counts() == {"finn": 1}


def test_window_open_while_nobody_home(home):
    from phrasehome.sensors import SensorWatcher

    sw = SensorWatcher(home)
    sw.values[("lounge", "window")] = {"node": "lounge", "input": "window", "state": "off"}  # open
    home.state.set_presence("jo", "home")
    home.sent.clear()
    home.state.set_presence("jo", "away")
    window = [s for s in home.sent if s[1] == "Window open"]
    assert len(window) == 4 and "lounge window" in window[0][2]
    home.state.set_presence("alex", "home")
    assert keys(home)[-1] == "windows_still_open"


# ---------------------------------------------------------------- winter lamp (31), pollen (35)
def test_winter_lamp_on_dark_mornings(home, monkeypatch):
    from phrasehome import automation

    monkeypatch.setattr(automation, "sun_is_down", lambda h, at=None: True)
    home.events.log("agenda.alarm", instance="finn", room="finn's room")
    assert {"node": "finn", "channel": "lamp", "state": True, "level": 100} in Recorder.calls
    assert keys(home)[-1] == "winter_lamp_on"
    n = len(Recorder.calls)
    home.events.log("agenda.alarm", instance="finn", room="finn's room")
    assert len(Recorder.calls) == n  # once a morning


def test_pollen_advice_names_who_has_hay_fever(home, monkeypatch):
    L = life(home)
    L.quick("parker has hay fever", room="kitchen")
    monkeypatch.setattr(L.watch, "pollen_level", lambda: ("high", 2))
    assert L.quick("should i open the windows", room="kitchen")
    assert keys(home)[-1] == "pollen_high" and "Parker" in home.said[-1][1]
    monkeypatch.setattr(L.watch, "pollen_level", lambda: ("low", 0))
    L.watch.window_advice("kitchen")
    assert keys(home)[-1] in ("pollen_ok", "pollen_unknown")


# ---------------------------------------------------------------- walk home (48)
def test_walk_home_overdue_tells_the_others_plainly(home):
    L = life(home)
    assert L.quick("parker's walking home from the library", room="kitchen")
    w = L.walk
    assert "parker" in w.active
    home.clock.advance(home, 26 * 60)
    w.tick()
    assert sorted(s[0] for s in home.sent) == ["alex", "finn", "jo"]
    assert "Parker said they'd be home by" in home.sent[0][2]
    assert "bloody" not in home.sent[0][2] and "fuck" not in home.sent[0][2]  # plain, even in aussie_uncut
    home.sent.clear()
    home.state.set_presence("parker", "home")
    assert "parker" not in w.active
    assert sorted(s[0] for s in home.sent) == ["alex", "finn", "jo"] and "home safe" in home.sent[0][2]


def test_walk_home_from_the_phone_topic(home):
    w = life(home).walk
    w.heard("walking home 30", title="Jo")
    assert "jo" in w.active
    w.heard("+10", title="Jo")
    assert w.active["jo"]["eta"] > time.time() + 39 * 60
    w.heard("home", title="Jo")
    assert "jo" not in w.active


# ---------------------------------------------------------------- quiz (54), radio
def test_weekly_quiz_from_the_weeks_events(home):
    from phrasehome.quiz import questions

    for _ in range(3):
        home.events.log("command", name="hush", room="kitchen")
    home.events.log("command", name="lockdown", room="kitchen")
    home.events.log("party.on", room="living room")
    qs = dict(questions(home))
    assert qs["How many times did someone tell me to shut up this week?"].startswith("3")
    assert "How many parties did this house have this week?" in qs
    q = life(home).quiz
    q.gap = 0.01
    q.start(wait=True)
    k = keys(home)
    assert k[0] == "quiz_start" and k.count("quiz_question") == k.count("quiz_answer") >= 2 and k[-1] == "quiz_end"


def test_radio_host_never_reads_lyrics(home):
    from phrasehome.radio import no_lyrics

    r = life(home).radio
    r.start()
    text = r.segment("Some Song by Some Band")
    assert "That was Some Song by Some Band" in text and "House Radio" in text
    sung = no_lyrics('[cheerful] Great tune. "And the words go on and on about the night we had" Back to it.')
    assert "words go on" not in sung and "Great tune" in sung
    r.stop()
    assert keys(home)[-1] == "radio_off"


def test_every_life_command_is_built_in(home):
    from phrasehome.config import LIFE_COMMANDS

    have = {c.action.get("life") for c in home.config.commands if c.action.get("backend") == "life"}
    assert have - {"quick"} == {n for n, _ in LIFE_COMMANDS}
    for what, _ in LIFE_COMMANDS:  # each one runs without an error, even with nothing going on
        home.run_action({"backend": "life", "life": what}, {"here": "kitchen"})


def test_said_to_the_house_it_needs_no_ai(home):
    import threading

    from phrasehome.assistant import Assistant, AssistantRouter
    from tests.test_assistant import FakeClient

    ai = Assistant(home, client=FakeClient())  # an empty script: any AI call would fail the test
    home.assistant = ai
    router = AssistantRouter(ai, transcribe=lambda pcm: "")
    assert router.handle("mic1", "kitchen", "house parker's a vegetarian", b"", None)
    for t in list(threading.enumerate()):
        if t.daemon and "_answer" in str(getattr(t, "_target", "")):
            t.join(2)
    assert "is vegetarian" in life(home).people.facts(home.instances.all["parker"])
    assert ai.client.requests == []


def test_house_page_and_api(home):
    import requests

    from phrasehome.web import WebServer

    home.config.web = {"port": 0, "host": "127.0.0.1"}
    web = WebServer(home)
    web.start()
    try:
        base = f"http://127.0.0.1:{web.port}"
        assert "Walking home" in requests.get(base + "/house", timeout=5).text
        r = requests.post(base + "/api/house/people", json={"person": "parker", "fact": "is vegetarian"}, timeout=5)
        assert r.ok and "vegetarian" in str(requests.get(base + "/api/house", timeout=5).json()["people"])
        assert requests.post(base + "/api/walkhome", json={"person": "jo", "minutes": 15}, timeout=5).ok
        assert requests.get(base + "/api/house", timeout=5).json()["walking"][0]["name"] == "Jo"
        assert requests.post(base + "/api/house", json={"action": "party_start"}, timeout=5).ok
        assert requests.get(base + "/api/house", timeout=5).json()["party"] is not None
        assert requests.post(base + "/api/house/agenda", json={"item": "bins"}, timeout=5).ok
        assert requests.get(base + "/api/house", timeout=5).json()["agenda"] == ["bins"]
    finally:
        web.stop()
