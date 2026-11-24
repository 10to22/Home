"""Freak mode, and the sweary style going clean for guests."""

import pytest

from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.voice import STYLES
from tests.test_door import Recorder  # noqa: F401
from tests.test_wake import Clock

CONFIG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
  phone: { type: record }
voice: { engine: none, style: aussie_uncut }
instances:
  household: { name: Household, main: true, rooms: [living room, kitchen] }
  finn: { name: Finn, rooms: [finn's room], phone: finn-topic }
  alex: { name: Alex, rooms: [alex's room], phone: alex-topic }
  jo: { name: Jo, rooms: [jo's room], phone: jo-topic }
  sky: { name: Sky, rooms: [sky's room] }
freak:
  minutes: 60
  on_start: [ { node: "{here}", channel: lamp, level: 15 } ]
commands:
  - { name: freak_on, phrases: [freak mode], action: { freak: on } }
  - { name: freak_off, phrases: [freak mode off], action: { freak: off } }
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    (tmp_path / "c.yaml").write_text(CONFIG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.voice.dry_run = True
    h.spoken, h.sent = [], []
    real = h.voice.speak
    h.voice.speak = lambda key, **v: h.spoken.append(real(key, **v)) or h.spoken[-1]
    monkeypatch.setattr(h, "notify_person", lambda inst, msg, title="": h.sent.append((inst.id, title, msg)))
    h.clock = Clock(monkeypatch)
    yield h
    h.close()


def test_freak_mode_tells_everyone_else_and_goes_quiet(home):
    home.run_action({"backend": "freak", "freak": "on"}, {"here": "finn's room"})
    who = sorted(s[0] for s in home.sent)
    assert who == ["alex", "jo"]  # not Finn (it's Finn's room), not Sky (no phone topic)
    assert all("freak" in s[2].lower() and "finn's room" in s[2] for s in home.sent)
    assert home.state.is_private("finn's room") and not home.state.is_private("kitchen")
    assert home.instances.dnd(home.instances.all["finn"])
    assert home.freak.status() == [{"room": "finn's room", "minutes_left": 60}]
    assert home.spoken[0].startswith("Freak mode on")


def test_freak_mode_off_by_voice_and_all_clear(home):
    home.run_action({"backend": "freak", "freak": "on"}, {"here": "finn's room"})
    home.sent.clear()
    home.run_action({"backend": "freak", "freak": "off"}, {"here": "finn's room"})
    assert sorted(s[0] for s in home.sent) == ["alex", "jo"]
    assert all("over" in s[2].lower() or "all clear" in s[2].lower() for s in home.sent)
    assert not home.state.is_private("finn's room")
    assert not home.instances.dnd(home.instances.all["finn"]) and home.freak.status() == []


def test_freak_mode_ends_by_itself(home):
    home.run_action({"backend": "freak", "freak": "on"}, {"here": "alex's room"})
    home.clock.advance(home, 59 * 60)
    assert home.freak.status()
    home.clock.advance(home, 61)
    assert home.freak.status() == [] and not home.state.is_private("alex's room")
    assert home.events.query("freak.off")[0]["why"] == "time's up"


def test_freak_off_gets_through_the_paused_mic():
    import inspect

    from phrasehome import listener

    src = inspect.getsource(listener)
    assert 'a.get("backend") == "freak"' in src


def test_freak_dashboard_api(home):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = home
    web.handle_post("/api/freak", {"action": "on", "room": "jo's room", "minutes": 30})
    assert home.freak.status()[0]["minutes_left"] == 30
    web.handle_post("/api/freak", {"action": "off", "room": "jo's room"})
    assert home.freak.status() == []


def test_swearing_goes_clean_for_guests(home):
    home.voice.speak("door_auto_locked", room="kitchen", door="front")
    assert home.spoken[-1] == STYLES["aussie_uncut"]["door_auto_locked"].format(door="front")
    home.state.set_mode("guest")
    home.voice.speak("door_auto_locked", room="kitchen", door="front")
    assert home.spoken[-1] == STYLES["aussie"]["door_auto_locked"].format(door="front")


def test_uncut_actually_swears_and_safety_stays_plain():
    lines = " ".join(str(v) for v in STYLES["aussie_uncut"].values()).lower()
    assert any(w in lines for w in ("fuck", "shit", "bastard"))
    assert "smoke_alarm" not in STYLES["aussie_uncut"]


def test_freak_commands_work_without_editing_config(tmp_path):
    from phrasehome.matcher import Matcher, expand

    (tmp_path / "c.yaml").write_text("""
backends: { dev: { type: log } }
settings: { default_backend: dev }
commands:
  - { phrases: [lights on], action: { backend: dev } }
""")
    cfg = load_config(tmp_path / "c.yaml")
    m = Matcher(expand(cfg.commands, cfg.slots), cfg.settings.match_mode, cfg.settings.match_threshold,
                cfg.settings.block_words)
    assert m.match("freak mode").command.name == "freak_on"
    assert m.match("freak mode off").command.name == "freak_off"


def test_builtin_commands_dont_duplicate_yours(tmp_path):
    (tmp_path / "c.yaml").write_text("""
backends: { dev: { type: log } }
settings: { default_backend: dev }
commands:
  - { name: sock_on, phrases: [sock on the door], action: { freak: on } }
""")
    names = [c.name for c in load_config(tmp_path / "c.yaml").commands]
    assert "sock_on" in names and "freak_on" not in names and "freak_off" in names
