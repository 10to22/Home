"""Numbers, "brighter", "turn them off", room groups, "... in five minutes", teach by voice, the go-on beep."""

import threading
import time
import types

from phrasehome.config import Command
from phrasehome.listener import find_commands, run_or_confirm
from phrasehome.matcher import Matcher
from phrasehome.understanding import candidates_with_aliases
from phrasehome.understanding_plus import (add_halves, pronoun_forms, spell_numbers, split_later,
                                           words_to_number)
from tests.test_assistant import FakeClient, home  # noqa: F401  (fixture)


def _m(cfg):
    s = cfg.settings
    return Matcher(candidates_with_aliases(cfg), s.match_mode, s.match_threshold, s.block_words)


def _setup(home, monkeypatch):
    c = home.config
    c.slots["level"] = {str(n): str(n) for n in ()}
    from phrasehome.clock import number_words

    c.slots["level"] = {number_words(n): str(n) for n in range(10, 101, 10)}
    c.commands.append(Command("room_lights_off", ["turn off the {room} lights"],
                              {"backend": "dev", "node": "{room}", "channel": "lights", "state": False}))
    c.commands.append(Command("dim", ["set the lights to {level} percent"],
                              {"backend": "dev", "node": "{here}", "channel": "lights", "level": "{level}"}))
    c.extra["relative"] = {"brightness": {"command": "dim", "slot": "level", "step": 20, "min": 10, "max": 100,
                                          "up": ["brighter"], "down": ["dimmer", "darker"], "start": 50}}
    c.extra["groups"] = {"upstairs": ["bedroom"], "downstairs": ["kitchen"]}
    sent = []
    monkeypatch.setattr(home, "submit", lambda m: sent.append(m) or True)
    return _m(c), sent


def test_numbers():
    assert words_to_number("seventy two and a half") == 72.5
    assert words_to_number("one hundred twenty") == 120
    assert words_to_number("72.5") == 72.5
    assert words_to_number("seventy-two") == 72
    assert words_to_number("lights") is None
    assert spell_numbers("set the heat to 72.5") == "set the heat to seventy two and a half"
    assert spell_numbers("dim to 30%") == "dim to thirty percent"
    assert add_halves({"seventy": "70"})["seventy and a half"] == "70.5"


def test_halves_in_the_config(tmp_path):
    from phrasehome.config import load_config
    from tests.test_assistant import AI_CONFIG

    (tmp_path / "c.yaml").write_text(AI_CONFIG.replace("slots:\n", "slots:\n  temp: { numbers: [60, 80], halves: true }\n"))
    cfg = load_config(tmp_path / "c.yaml")
    assert cfg.slots["temp"]["seventy two and a half"] == "72.5"


def test_digits_match_spelled_phrases(home, monkeypatch):
    m, sent = _setup(home, monkeypatch)
    got, delays, say = find_commands("set the lights to 40 percent", m, home, home.config, "kitchen")
    assert got[0].command.name == "dim" and got[0].slots["level"] == "40"


def test_brighter_and_a_bit_dimmer(home, monkeypatch):
    m, sent = _setup(home, monkeypatch)
    got, _, _ = find_commands("brighter", m, home, home.config, "kitchen")
    assert got[0].slots == {"level": "70", "here": "kitchen"}  # from the start (50) one step up
    run_or_confirm(got[0], home, home.config)
    got, _, _ = find_commands("a bit dimmer", m, home, home.config, "kitchen")
    assert got[0].slots["level"] == "60"  # half a step down from 70


def test_turn_them_off(home, monkeypatch):
    m, sent = _setup(home, monkeypatch)
    assert "turn them off" in pronoun_forms("turn off the kitchen lights", ["kitchen"])
    first = m.match("turn on the kitchen lights")
    first.room = "kitchen"
    run_or_confirm(first, home, home.config)
    got, _, _ = find_commands("turn them off", m, home, home.config, "kitchen")
    assert [(x.command.name, x.slots) for x in got] == [("room_lights_off", {"room": "kitchen"})]
    assert find_commands("turn them off", m, home, home.config, "bedroom") is None  # nothing said there


def test_groups(home, monkeypatch):
    m, sent = _setup(home, monkeypatch)
    got, _, _ = find_commands("turn off the upstairs lights", m, home, home.config, "kitchen")
    assert [x.slots for x in got] == [{"room": "bedroom"}]
    got, _, _ = find_commands("turn off the lights everywhere", m, home, home.config, "kitchen") or ([], 0, 0)
    got2, _, _ = find_commands("turn off the everywhere lights", m, home, home.config, "kitchen")
    assert sorted(x.slots["room"] for x in got2) == ["bedroom", "kitchen"]


def test_timed_chains(home, monkeypatch):
    m, sent = _setup(home, monkeypatch)
    assert split_later("turn off the lights in five minutes") == ("turn off the lights", 300.0)
    assert split_later("lockdown in a minute") == ("lockdown", 60.0)
    got, delays, _ = find_commands("turn on the kitchen lights then turn them off in ten seconds",
                                   m, home, home.config, "kitchen")
    assert [x.command.name for x in got] == ["room_lights_on", "room_lights_off"] and delays == [0.0, 10.0]
    later = []
    home.timeline = types.SimpleNamespace(call_in=lambda s, fn: later.append((s, fn)))
    from phrasehome.listener import run_all

    run_all(got, delays, home, home.config, "kitchen", None, None, 1.0)
    assert [x.command.name for x in sent] == ["room_lights_on"] and later[0][0] == 10.0
    later[0][1]()
    assert [x.command.name for x in sent] == ["room_lights_on", "room_lights_off"]


def test_teach_by_voice(home, monkeypatch):
    from phrasehome.customs import Customs

    m, sent = _setup(home, monkeypatch)
    Customs(home)
    got = find_commands("when i say cozy mode turn on the kitchen lights and lockdown", m, home, home.config,
                        "kitchen", allow_teach=True)
    assert got[0] == [] and "cozy mode" in got[2]
    assert any(c.name == "custom_cozy_mode" for c in home.config.commands)
    # without the name, "when I say ..." isn't taken as teaching
    assert find_commands("when i say party time turn on the kitchen lights", m, home, home.config, "kitchen") is None


def test_half_heard_name_beeps_instead_of_guessing(home):
    from phrasehome.assistant import Assistant, AssistantRouter

    ai = Assistant(home, client=FakeClient())
    asked = []
    ai.ask = lambda q, room=None, person=None, on_text=None: asked.append(q) or "Sure."
    r = AssistantRouter(ai, transcribe=lambda pcm: "")
    assert r.handle("mic1", "kitchen", "mouse the thing", b"", None, woke=True)
    assert home.spoken[-1] == "go_on_sound" and "mic1" in r._follow_up
    assert r.handle("mic1", "kitchen", "what's the weather", b"", None)  # the follow-up
    for t in list(threading.enumerate()):
        if t.daemon and "_answer" in str(getattr(t, "_target", "")):
            t.join(2)
    assert asked == ["what's the weather"]
    r._follow_up.clear()
    assert r.handle("mic2", "hall", "mouse what time is it", b"", None, woke=True)  # clear enough: answered
    time.sleep(0.05)
