"""The name anywhere, "stop listening", "I wasn't talking to you", learning from mistakes, speaker
rules, "no, I meant the bedroom", and the openWakeWord engine."""

import threading
import time
import types

import numpy as np

from phrasehome.assistant import Assistant, AssistantRouter
from phrasehome.attention import examples_for, no_name_needed, quiet_request
from phrasehome.hearing import OpenWakeWordSpotter
from phrasehome.listener import correction, handle_result, run_or_confirm
from phrasehome.understanding import intent
from tests.test_assistant import FakeClient, home, text  # noqa: F401  (home is a fixture)
from tests.test_wordbank import _matcher


def _wait():
    for t in list(threading.enumerate()):
        if t.name != "MainThread" and t.daemon and t.is_alive() and "_answer" in str(t._target):
            t.join(2)


def _router(home, answers=("Sure.",)):
    asked = []
    ai = Assistant(home, client=FakeClient())
    home.assistant = ai
    replies = iter(list(answers) * 10)
    ai.ask = lambda q, room=None, person=None, on_text=None: asked.append(q) or next(replies)
    return AssistantRouter(ai, transcribe=lambda pcm: ""), asked


# ---------- the name at the end ----------
def test_name_at_the_end(home):
    ai = Assistant(home, client=FakeClient())
    assert ai.addressed("turn off the kitchen lights house") == "turn off the kitchen lights"
    assert ai.addressed("what time is it jarvis") == "what time is it"
    assert ai.addressed("so what's the weather house please") == "what's the weather"
    for said in ("i love this house", "time to go home", "welcome home", "take me home", "lock the house",
                 "the house is cold", "party at my house"):
        assert ai.addressed(said) is None, said
    home.config.assistant["name_anywhere"] = False
    assert ai.addressed("turn off the kitchen lights house") is None


# ---------- stop listening ----------
def test_quiet_requests():
    assert quiet_request("stop listening") == ("stop", 30.0)
    assert quiet_request("stop listening for twenty minutes") == ("stop", 20.0)
    assert quiet_request("stop listening for 2 hours") == ("stop", 120.0)
    assert quiet_request("stop listening for half an hour") == ("stop", 30.0)
    assert quiet_request("give us some privacy") == ("stop", 30.0)
    assert quiet_request("start listening") == ("start", 0.0)
    assert quiet_request("you can listen again") == ("start", 0.0)
    assert quiet_request("stop the music") is None


def test_stop_listening_keeps_commands_working(home):
    router, asked = _router(home)
    ran = []
    router.try_command = lambda t, *a, **k: (t == "lockdown" and not ran.append(t)) or False
    assert router.handle("mic1", "kitchen", "house stop listening for ten minutes", b"", None)
    assert router.quiet.on("kitchen") and "10 minutes" in home.spoken[-1]
    assert router.handle("mic1", "kitchen", "house what's the weather", b"", None)  # taken, not answered
    assert router.handle("mic1", "kitchen", "house lockdown", b"", None)
    assert not router.handle("mic1", "kitchen", "lockdown", b"", None)  # no name: the matcher's job
    assert not router.quiet.on("bedroom")  # only that room
    _wait()
    assert asked == [] and ran == ["lockdown"]
    assert router.handle("mic1", "kitchen", "house start listening", b"", None)
    assert not router.quiet.on("kitchen")
    assert router.handle("mic1", "kitchen", "house what's the weather", b"", None)
    _wait()
    assert asked == ["what's the weather"]


def test_quiet_ends_on_its_own(home):
    router, _ = _router(home)
    router.quiet.stop("kitchen", 1)
    router.quiet.until["kitchen"] = time.monotonic() - 1
    assert not router.quiet.on("kitchen")


# ---------- learning ----------
def test_not_talking_to_you_is_remembered(home):
    router, asked = _router(home)
    hushed = []
    home.voice.hush = lambda: hushed.append(1) or "ok"
    assert router.handle("mic1", "kitchen", "house what's that noise", b"", None)
    _wait()
    assert asked == ["what's that noise"]
    assert router.handle("mic1", "kitchen", "i wasn't talking to you", b"", None)  # no name needed
    assert hushed == [1]
    assert examples_for(home).lookup("what's that noise") is False
    assert intent(home, "what's that noise") == "ignore"  # learned: no AI call, no answer
    assert router.handle("mic1", "kitchen", "house what's that noise", b"", None)
    _wait()
    assert asked == ["what's that noise"]  # not asked again
    # "not you" long after an answer is just words
    router._last_answer.clear()
    assert not router.handle("mic1", "kitchen", "not you", b"", None)


def test_ignored_then_said_again_means_it_was_for_the_house(home):
    router, asked = _router(home)
    home.assistant.client = FakeClient([text('{"kind": "neither"}')])
    assert router.handle("mic1", "kitchen", "house party music", b"", None)
    _wait()
    assert asked == []
    assert router.handle("mic1", "kitchen", "house party music", b"", None)  # again, within 30 s
    _wait()
    assert asked == ["party music"]
    assert examples_for(home).lookup("party music") is True


def test_learned_examples_reach_the_ai(home):
    ai = Assistant(home, client=FakeClient([text('{"kind": "task"}')]))
    home.assistant = ai
    examples_for(home).add("movie vibes", True, "test")
    intent(home, "something cosy")
    assert "movie vibes" in ai.client.requests[0]["messages"][0]["content"][-1]["text"]
    home.config.assistant["learn"] = False
    home.__dict__.pop("_intent_examples")
    assert examples_for(home).lookup("movie vibes") is None


# ---------- speaker rules ----------
def test_no_name_needed_in_your_own_room():
    sam = types.SimpleNamespace(name="Sam", rooms=["bedroom"])
    home = types.SimpleNamespace(config=types.SimpleNamespace(assistant={"no_name_needed": ["sam"]}),
                                 instances=types.SimpleNamespace(all={"sam": sam}))
    a = types.SimpleNamespace(home=home)
    assert no_name_needed(a, "sam", "bedroom")
    assert not no_name_needed(a, "sam", "kitchen")  # not Sam's room
    assert not no_name_needed(a, None, "bedroom")  # voice not recognized
    home.config.assistant = {}
    assert not no_name_needed(a, "sam", "bedroom")


def test_owner_is_answered_without_the_name(home, monkeypatch):
    import phrasehome.attention as att

    router, asked = _router(home)
    monkeypatch.setattr(att, "no_name_needed", lambda a, person, room: person == "sam" and room == "bedroom")
    assert router.handle("mic1", "bedroom", "what's the weather tomorrow", b"", None, person="sam")
    assert not router.handle("mic1", "kitchen", "what's the weather tomorrow", b"", None, person="sam")
    assert not router.handle("mic1", "bedroom", "what's the weather tomorrow", b"", None, person=None)
    _wait()
    assert asked == ["what's the weather tomorrow"]


def test_strangers_need_the_name_for_commands(home, monkeypatch):
    import phrasehome.listener as listener

    ran = []
    monkeypatch.setattr(listener, "run_or_confirm", lambda m, d, c: ran.append(m.command.name))
    home.voice_id = types.SimpleNamespace(cfg={"strangers_need_name": True}, prints={"sam": [1]})
    m = _matcher(home.config)
    result = {"text": "turn on the kitchen lights", "result": []}
    handle_result(result, m, home, home.config, room="kitchen", unknown_voice=True)  # the TV
    assert ran == []
    handle_result(result, m, home, home.config, room="kitchen", unknown_voice=False)  # someone it knows
    assert ran == ["room_lights_on"]
    home.voice_id.cfg = {}
    handle_result(result, m, home, home.config, room="kitchen", unknown_voice=True)
    assert ran == ["room_lights_on", "room_lights_on"]


# ---------- "no, I meant the bedroom" ----------
def test_correct_the_last_command(home, monkeypatch):
    sent = []
    monkeypatch.setattr(home, "submit", lambda m: sent.append(m) or True)
    m = _matcher(home.config)
    first = m.match("turn on the kitchen lights")
    first.room = "kitchen"
    run_or_confirm(first, home, home.config)
    fix = correction("no i meant the bedroom", m, home, home.config, "kitchen")
    assert [(x.command.name, x.slots) for x in fix] == [("room_lights_on", {"room": "bedroom"})]
    assert fix[0].via == "correction"
    fix = correction("no not the kitchen the bedroom", m, home, home.config, "kitchen")
    assert fix[0].slots == {"room": "bedroom"}
    assert correction("actually lockdown", m, home, home.config, "kitchen")[0].command.name == "lockdown"
    assert correction("i meant the bedroom", m, home, home.config, "bedroom") == []  # other room: nothing
    assert correction("the bedroom", m, home, home.config, "kitchen") == []  # not a correction
    home._last_command["kitchen"] = (time.monotonic() - 60, first)
    assert correction("no i meant the bedroom", m, home, home.config, "kitchen") == []  # too late


# ---------- openWakeWord ----------
def test_openwakeword_spotter():
    class FakeModel:
        def __init__(self, inference_framework="onnx", wakeword_models=()):
            self.framework, self.models, self.frames = inference_framework, list(wakeword_models), 0

        def predict(self, frame):
            assert len(frame) == 1280
            self.frames += 1
            return {"hey_home": 0.9 if self.frames == 3 else 0.1}

    sp = OpenWakeWordSpotter(["models/hey_home.onnx"], 0.5, model_class=FakeModel)
    chunk = np.zeros(2000, dtype=np.int16).tobytes()
    assert not sp.feed(chunk, 1.0)  # one frame, 720 samples left over
    assert sp.feed(chunk, 2.0)  # frames 2 and 3: heard
    assert sp.heard_recently(4.0, window=3) and not sp.heard_recently(9.0, window=3)
    assert sp.model.models == ["models/hey_home.onnx"]
