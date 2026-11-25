"""'Shut up' stops the house mid-word; 'fire' is a command; 'couldn't hear you'; local AI context."""

import json
import types

import pytest

from phrasehome import audio_out
from phrasehome.config import load_config
from phrasehome.listener import Engine
from tests.test_door import Recorder  # noqa: F401

CFG = """
backends: { dev: { type: record } }
settings: { default_backend: dev }
voice: { engine: none, style: plain }
routines:
  fire_mode: [ { node: all, channel: lights, state: on } ]
commands:
  - { name: alarm_stop, phrases: [stop the alarm, stop], action: { alarm: stop } }
  - { name: lights_on, phrases: [lights on], action: { node: kitchen, channel: lights, state: on } }
"""


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG)
    return load_config(tmp_path / "c.yaml")


def test_builtin_hush_and_fire_commands(cfg, tmp_path):
    names = {c.name: c for c in cfg.commands}
    assert "shut up" in names["hush"].phrases and names["hush"].action == {"backend": "voice", "hush": True}
    assert "fire" in names["fire"].phrases and names["fire"].action["routine"] == "fire_mode"
    (tmp_path / "d.yaml").write_text(CFG.replace("fire_mode", "something_else"))
    assert "fire" not in {c.name for c in load_config(tmp_path / "d.yaml").commands}  # no routine, no command


def test_hush_kills_the_sound_mid_word(monkeypatch):
    killed = []

    class P:
        def kill(self):
            killed.append(1)

    monkeypatch.setattr(audio_out, "_procs", {P(), P()})
    assert audio_out.hush_local() == 2 and len(killed) == 2


class FakeRec:
    def __init__(self, partial):
        self.partial, self.resets = partial, 0

    def AcceptWaveform(self, pcm):
        return False

    def PartialResult(self):
        return json.dumps({"partial": self.partial})

    def Reset(self):
        self.resets += 1


def _engine(cfg, partial, monkeypatch):
    hushed, submitted = [], []
    e = Engine.__new__(Engine)
    e.config, e.buffers, e._stops = cfg, {"local": bytearray(b"x")}, None
    rec = FakeRec(partial)
    e._recognizer = lambda source, room=None: rec
    e.dispatcher = types.SimpleNamespace(voice=types.SimpleNamespace(hush=lambda: hushed.append(1)),
                                         submit=submitted.append)
    return e, rec, hushed, submitted


def test_shut_up_heard_over_the_house_talking(cfg, monkeypatch):
    monkeypatch.setattr(audio_out, "speaking_text", "Rain all day. Take a brolly, ya galah.")
    e, rec, hushed, submitted = _engine(cfg, "rain all day take a shut up", monkeypatch)
    e._while_talking("local", "kitchen", b"..", None)
    assert hushed == [1] and rec.resets == 1 and submitted == []


def test_the_house_saying_it_isnt_you_saying_it(cfg, monkeypatch):
    monkeypatch.setattr(audio_out, "speaking_text", "Oh shut up and get out of bed.")
    e, rec, hushed, _ = _engine(cfg, "oh shut up and get out", monkeypatch)
    e._while_talking("local", "bedroom", b"..", None)
    assert hushed == []


def test_other_commands_wait_until_it_stops_talking(cfg, monkeypatch):
    monkeypatch.setattr(audio_out, "speaking_text", "Good morning.")
    e, _, hushed, submitted = _engine(cfg, "lights on", monkeypatch)
    e._while_talking("local", "kitchen", b"..", None)
    assert hushed == [] and submitted == []


def test_stop_during_the_alarm_also_stops_the_alarm(cfg, monkeypatch):
    monkeypatch.setattr(audio_out, "speaking_text", "It's 6:45. Up you get.")
    e, _, hushed, submitted = _engine(cfg, "up you get stop", monkeypatch)
    e._while_talking("local", "bedroom", b"..", None)
    assert hushed == [1] and submitted[0].command.name == "alarm_stop" and submitted[0].room == "bedroom"


# ---------- "couldn't hear you" ----------
def test_unclear_questions(monkeypatch):
    from phrasehome.assistant import AssistantRouter

    said = []
    a = types.SimpleNamespace(min_confidence=0.55, follow_up_seconds=8,
                              home=types.SimpleNamespace(voice=types.SimpleNamespace(speak=lambda k, **v: said.append(k))))
    t = types.SimpleNamespace(last_confidence=0.3)
    r = AssistantRouter.__new__(AssistantRouter)
    r.assistant, r.transcribe = a, t
    assert r._unclear("fish blue the of", spoken=True)  # unsure
    t.last_confidence = 0.9
    assert not r._unclear("what's the weather", spoken=True)
    assert r._unclear("unk unk", spoken=True) and r._unclear("", spoken=False)
    assert not r._unclear("what's the weather", spoken=False)  # typed: no confidence to check


def test_couldnt_hear_lines_in_every_style():
    from phrasehome.voice import DEFAULT_PHRASES, STYLES

    assert DEFAULT_PHRASES["didnt_catch"]
    for name in ("mean", "aussie", "aussie_uncut", "deadpan"):
        assert STYLES[name]["didnt_catch"]


# ---------- local AI: room for the whole prompt, and a warm-up ----------
def test_local_ai_asks_for_a_big_enough_context(monkeypatch):
    from tests.test_local_ai import FakeOllama

    f = FakeOllama()
    try:
        f.say("ok")
        from phrasehome.local_ai import LocalClient

        LocalClient({"url": f.url}).messages.create(messages=[{"role": "user", "content": "hi"}])
        assert f.requests[0]["options"]["num_ctx"] == 16384
    finally:
        f.httpd.shutdown()


def test_normal_listening_still_runs_commands(cfg, monkeypatch):
    """When the house isn't talking, what's heard goes through the normal path to the command."""
    from phrasehome.matcher import Matcher, expand

    monkeypatch.setattr(audio_out, "local_busy", lambda: False)
    handled = []
    monkeypatch.setattr("phrasehome.listener.handle_result", lambda result, *a, **k: handled.append(result["text"]))

    class Rec:
        def AcceptWaveform(self, pcm):
            return True

        def Result(self):
            return json.dumps({"text": "lights on"})

    e = Engine.__new__(Engine)
    e.config, e.buffers, e._stops, e.router = cfg, {}, None, None
    e._recognizer = lambda source, room=None: Rec()
    s = cfg.settings
    lang = types.SimpleNamespace(lang="en", matcher=Matcher(expand(cfg.commands, cfg.slots), s.match_mode,
                                                            s.match_threshold, s.block_words))
    e._lang = lambda room: lang
    e.dispatcher = types.SimpleNamespace(state=types.SimpleNamespace(is_private=lambda r: False))
    e.feed("local", "kitchen", b"\x00\x00" * 100, None)
    assert handled == ["lights on"]
