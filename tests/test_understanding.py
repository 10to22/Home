"""Did you mean, confirming risky commands, the History page and teaching."""

import time
import types

import pytest

from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.listener import handle_result
from phrasehome.matcher import Matcher
from phrasehome.understanding import Aliases, candidates_with_aliases, is_risky, is_sure
from tests.test_door import Recorder  # noqa: F401
from tests.test_wake import Clock

CFG = """
data_dir: data
backends: { dev: { type: record } }
settings: { default_backend: dev, log_heard: true, cooldown_seconds: 0, arbitration_seconds: 0 }
voice: { engine: none, style: plain }
doors:
  front: { lock: { node: front, channel: lock, state: on }, unlock: { node: front, channel: lock, state: off } }
routines:
  lockdown: [ { door: all, state: locked } ]
  fire_mode: [ { node: all, channel: siren, state: on } ]
commands:
  - { name: lights_on, phrases: [lights on, turn on the lights], action: { node: kitchen, channel: lights, state: on } }
  - { name: lockdown, phrases: [lockdown], action: { routine: lockdown } }
  - { name: coffee, phrases: [make coffee], action: { node: kitchen, channel: coffee, state: on } }
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "c.yaml").write_text(CFG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.voice.dry_run = True
    h.spoken = []
    real = h.voice.speak
    h.voice.speak = lambda key, **v: h.spoken.append(real(key, **v)) or h.spoken[-1]
    h.clock = Clock(monkeypatch)
    yield h
    h.close()


def matcher(h):
    s = h.config.settings
    return Matcher(candidates_with_aliases(h.config), s.match_mode, s.match_threshold, s.block_words)


def hear(h, text, conf=0.95, room="kitchen"):
    words = [{"word": w, "conf": conf} for w in text.split()]
    handle_result({"text": text, "result": words}, matcher(h), h, h.config, room=room)


def ran(h, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not Recorder.calls:
        time.sleep(0.02)
    return Recorder.calls




def test_matcher_near(home):
    near = matcher(home).near("light zone")
    assert near and near[0][1].command.name == "lights_on" and near[0][0] < 85
    assert matcher(home).near("what a lovely day it is outside") == []


def test_risky_and_sure(home):
    cmds = {c.name: c for c in home.config.commands}
    assert is_risky(cmds["lockdown"]) and is_risky(cmds["fire"]) and not is_risky(cmds["lights_on"])
    assert is_risky(cmds["freak_on"]) and not is_risky(cmds["freak_off"])
    m = types.SimpleNamespace(score=100, confidence=0.95, via="voice")
    assert is_sure(m) and not is_sure(types.SimpleNamespace(score=100, confidence=0.6, via="voice"))
    assert not is_sure(types.SimpleNamespace(score=100, confidence=0.95, via="did-you-mean"))


def test_sure_commands_just_run(home):
    hear(home, "lights on")
    assert ran(home) == [{"node": "kitchen", "channel": "lights", "state": True}]
    e = home.history.entries()[0]
    assert (e["kind"], e["matched"], e["heard"]) == ("command", "lights_on", "lights on")


def test_unsure_lockdown_asks_and_yes_runs_it_and_learns(home):
    hear(home, "lockdown", conf=0.65)
    assert Recorder.calls == [] and home.spoken[-1] == "Did you say lockdown? Say yes or no."
    assert home.confirm.answer("kitchen", "yes")
    assert {"node": "front", "channel": "lock", "state": True} in ran(home)
    assert Aliases(home.config).all()[0]["command"] == "lockdown"  # that wording is remembered
    kinds = [e["kind"] for e in home.history.entries()]
    assert "asked" in kinds and "confirm" in kinds


def test_no_cancels(home):
    hear(home, "lockdown", conf=0.65)
    assert home.confirm.answer("kitchen", "no")
    time.sleep(0.2)
    assert Recorder.calls == [] and home.spoken[-1] == "Okay, never mind."


def test_something_else_drops_the_question(home):
    hear(home, "lockdown", conf=0.65)
    assert not home.confirm.answer("kitchen", "make coffee")
    assert not home.confirm.pending


def test_no_answer_cancels_but_fire_goes_ahead(home):
    hear(home, "lockdown", conf=0.65)
    home.clock.advance(home, 9)
    time.sleep(0.2)
    assert Recorder.calls == []
    hear(home, "fire", conf=0.65)
    assert "Did you say fire?" in home.spoken[-1]
    home.clock.advance(home, 9)
    assert {"node": "all", "channel": "siren", "state": True} in ran(home)


def test_did_you_mean_runs_what_was_meant(home, monkeypatch):
    from phrasehome import ai

    asked = {}

    def fake_ask(h, prompt, schema=None, system="", **kw):
        asked["prompt"], asked["enum"] = prompt, schema["properties"]["command"]["enum"]
        return {"command": "lights_on"}

    monkeypatch.setattr(ai, "ask", fake_ask)
    hear(home, "light zone")
    assert ran(home) == [{"node": "kitchen", "channel": "lights", "state": True}]
    assert "light zone" in asked["prompt"] and "lights_on" in asked["enum"] and "none" in asked["enum"]
    e = home.history.entries()[0]
    assert e["via"] == "did-you-mean" and e["matched"] == "lights_on"


def test_did_you_mean_none_does_nothing(home, monkeypatch):
    from phrasehome import ai

    monkeypatch.setattr(ai, "ask", lambda *a, **k: {"command": "none"})
    hear(home, "light zone")
    time.sleep(0.3)
    assert Recorder.calls == [] and home.history.entries()[0]["kind"] == "none"


def test_did_you_mean_never_skips_the_safety_question(home, monkeypatch):
    from phrasehome import ai

    monkeypatch.setattr(ai, "ask", lambda *a, **k: {"command": "lockdown"})
    hear(home, "lock town")
    end = time.monotonic() + 2
    while time.monotonic() < end and not home.confirm.pending:
        time.sleep(0.02)
    assert home.confirm.pending and Recorder.calls == []


def test_history_page_teach_and_unteach(home):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = home
    hear(home, "make cough he")  # misheard; no command
    entry = web.history_get()["entries"][0]
    assert entry["kind"] == "none"
    web.history_post("teach", {"id": entry["id"], "command": "coffee"})
    assert web.history_get()["aliases"][0] == {**web.history_get()["aliases"][0], "heard": "make cough he",
                                               "command": "coffee"}
    hear(home, "make cough he")
    assert ran(home) == [{"node": "kitchen", "channel": "coffee", "state": True}]
    assert web.history_get("cough")["entries"] and not web.history_get("zebra")["entries"]
    web.history_post("unteach", {"heard": "make cough he"})
    assert web.history_get()["aliases"] == []
    web.history_post("clear", {})
    assert web.history_get()["entries"] == []


def test_typed_commands_are_recorded_and_never_asked(home):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = home
    assert web.heard("lockdown", "kitchen")["matched"] == "lockdown"
    assert {"node": "front", "channel": "lock", "state": True} in ran(home)
    e = home.history.entries()[0]
    assert e["via"] == "typed" and not home.confirm.pending


def test_history_off_by_default_on_the_real_house(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG.replace("log_heard: true, ", ""))
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    assert not h.history.enabled and h.history.add(kind="x") is None
    h.close()
