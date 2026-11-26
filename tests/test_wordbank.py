"""The word bank, the house's name ("house" / "home" / "hey home"), ignoring remarks that aren't for
the house, and commands that run with or without the name."""

import threading

from phrasehome.assistant import Assistant, AssistantRouter
from phrasehome.listener import commands_in
from phrasehome.matcher import Matcher
from phrasehome.understanding import candidates_with_aliases, intent
from phrasehome.wordbank import quick_intent, strip_filler, variants
from tests.test_assistant import FakeClient, home, text  # noqa: F401  (home is a fixture)


def _wait():
    for t in list(threading.enumerate()):
        if t.name != "MainThread" and t.daemon and t.is_alive() and "_answer" in str(t._target):
            t.join(2)


# ---------- word bank ----------
def test_other_ways_of_saying_a_command():
    v = variants("turn on the {room} lights")
    assert "switch on the {room} lights" in v and "{room} lights on" in v and "turn the {room} lights on" in v
    assert "turn on the lights" in variants("lights on")
    assert "secure the {door} door" in variants("lock the {door} door")
    assert variants("movie time") == []  # nothing sensible to add


def test_polite_filler_is_dropped():
    assert strip_filler("could you please turn on the lights for me") == "turn on the lights"
    assert strip_filler("um lock the doors thanks") == "lock the doors"
    assert strip_filler("please") == "please"  # never down to nothing


def test_quick_intent():
    assert quick_intent("what time is it") == "question"
    assert quick_intent("how are you") == "question"
    assert quick_intent("is the back door locked?") == "question"
    assert quick_intent("turn off the kitchen lights") == "task"
    assert quick_intent("can you lock up") == "task"
    assert quick_intent("we're out of milk") == "task"
    # left to the AI: the name might just be a word in the sentence
    for said in ("party was fun", "is so messy", "sweet home", "what a day", "how cool is that", "thank you"):
        assert quick_intent(said) is None, said


def _matcher(cfg):
    s = cfg.settings
    return Matcher(candidates_with_aliases(cfg), s.match_mode, s.match_threshold, s.block_words)


def test_matcher_hears_more_wordings(home):
    m = _matcher(home.config)
    assert m.match("switch on the kitchen lights").command.name == "room_lights_on"
    assert m.match("turn the bedroom lights on").slots == {"room": "bedroom"}
    assert m.match("kitchen lights on").command.name == "room_lights_on"  # only thanks to the word bank
    assert m.match("could you please turn on the bedroom lights for me").command.name == "room_lights_on"
    assert m.match("don't turn on the kitchen lights") is None  # still blocked
    home.config.settings.phrase_variants = False
    assert _matcher(home.config).match("kitchen lights on") is None


def test_two_commands_in_one_sentence(home):
    m = _matcher(home.config)
    got = commands_in("lockdown and turn on the kitchen lights", m, home.config)
    assert [x.command.name for x in got] == ["lockdown", "room_lights_on"]
    assert commands_in("lockdown and make me a sandwich", m, home.config) == []  # every part must be one
    home.config.settings.multi_commands = False
    assert commands_in("lockdown and turn on the kitchen lights", m, home.config) == []


# ---------- the name ----------
def test_home_and_hey_home_are_names_by_default(home):
    home.config.assistant.pop("names", None)
    ai = Assistant(home, client=FakeClient())
    assert ai.addressed("hey home what time is it") == "what time is it"
    assert ai.addressed("house turn on the lights") == "turn on the lights"
    assert ai.addressed("home, lock up") == "lock up"
    assert ai.addressed("i'm home") is None


# ---------- not for the house ----------
def test_ai_decides_the_unclear_ones(home):
    ai = Assistant(home, client=FakeClient([text('{"kind": "neither"}')], [text('{"kind": "task"}')]))
    home.assistant = ai
    assert intent(home, "party was fun") == "ignore"
    assert intent(home, "it's freezing in here") == "task"
    assert ai.client.requests[0]["model"] == ai.fast_model  # the quick model, not the smart one
    assert intent(home, "what's the time") == "question"  # obvious: no AI call
    assert len(ai.client.requests) == 2


def test_if_the_ai_is_down_it_answers_as_before(home):
    def boom(**kw):
        raise RuntimeError("offline")

    ai = Assistant(home, client=FakeClient())
    ai.client.beta.messages.create = boom
    home.assistant = ai
    assert intent(home, "party was fun") == "question"


def test_remarks_with_the_name_are_ignored(home):
    asked = []
    ai = Assistant(home, client=FakeClient([text('{"kind": "neither"}')]))
    home.assistant = ai
    ai.ask = lambda q, room=None, person=None, on_text=None: asked.append(q) or "Sure."
    router = AssistantRouter(ai, transcribe=lambda pcm: "")
    assert router.handle("mic1", "kitchen", "house party was fun", b"", None)  # taken, then ignored
    _wait()
    assert asked == [] and home.spoken == []
    assert router.handle("mic1", "kitchen", "house what's the weather", b"", None)
    _wait()
    assert asked == ["what's the weather"]


def test_commands_run_with_or_without_the_name(home):
    ran, asked = [], []
    home.config.assistant.pop("names", None)  # the defaults: house, home
    ai = Assistant(home, client=FakeClient())
    ai.ask = lambda q, room=None, person=None, on_text=None: asked.append(q) or "Sure."
    router = AssistantRouter(ai, transcribe=lambda pcm: "")
    m = _matcher(home.config)

    def try_command(t, room, reply, person=None, lang=None, confidence=None, source=None):
        got = commands_in(t, m, home.config)
        ran.extend(x.command.name for x in got)
        return bool(got)

    router.try_command = try_command
    assert router.handle("mic1", "kitchen", "home switch on the kitchen lights", b"", None)
    assert router.handle("mic1", "kitchen", "hey house lockdown", b"", None)
    _wait()
    assert ran == ["room_lights_on", "lockdown"] and asked == []  # straight to the command, no AI
    assert not router.handle("mic1", "kitchen", "turn on the kitchen lights", b"", None)  # no name: matcher's job


def test_listener_runs_named_commands_and_respects_confidence(home, monkeypatch):
    import types

    from phrasehome import listener
    from phrasehome.listener import Engine

    ran = []
    monkeypatch.setattr(listener, "run_or_confirm", lambda m, d, c: ran.append((m.command.name, m.room)))
    e = Engine.__new__(Engine)
    e.config, e.dispatcher, e._word_conf = home.config, home, {"mic1": 0.9, "mic2": 0.3}
    lang = types.SimpleNamespace(lang="en", matcher=_matcher(home.config))
    e.langs, e._lang = {"en": lang}, lambda room: lang
    assert e._try_command("lockdown and turn on the kitchen lights", "kitchen", None, source="mic1")
    assert ran == [("lockdown", "kitchen"), ("room_lights_on", "kitchen")]
    assert not e._try_command("lockdown", "kitchen", None, source="mic2")  # too unsure: not run
    assert not e._try_command("lock unk door", "kitchen", None, source="mic1")  # half heard: AI writes it down
    assert not e._try_command("what's the weather", "kitchen", None, source="mic1")
    assert len(ran) == 2
