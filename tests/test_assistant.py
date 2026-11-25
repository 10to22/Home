"""AI assistant tests with a scripted fake Claude client (no API calls)."""

from types import SimpleNamespace as NS

import pytest

from phrasehome.assistant import Assistant, AssistantRouter, spoken
from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from tests.test_door import CONFIG, Recorder


def text(t):
    return NS(type="text", text=t)


def tool(tool_name, **inp):
    return NS(type="tool_use", id=f"tu_{tool_name}", name=tool_name, input=inp)


class FakeClient:
    """Plays back a script of responses and records every request."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})
        content = self.script.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return NS(content=content, stop_reason=stop)


AI_CONFIG = CONFIG + """
slots:
  room: [kitchen, bedroom]
assistant:
  names: [house, jarvis]
  location: Portland, Oregon
"""
AI_CONFIG = AI_CONFIG.replace("""commands:
  - phrases: [lockdown]
    action: { routine: lockdown }""", """commands:
  - phrases: [lockdown]
    action: { routine: lockdown }
  - name: room_lights_on
    phrases: ["turn on the {room} lights"]
    action: { node: "{room}", channel: lights, state: on }
  - name: lights_here
    phrases: [lights on]
    action: { node: "{here}", channel: lights, state: on }""")


@pytest.fixture
def home(tmp_path):
    (tmp_path / "c.yaml").write_text(AI_CONFIG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.spoken = []
    h.voice.speak = lambda key, **v: h.spoken.append(key) or key
    yield h
    h.close()


def test_runs_routine_and_answers(home):
    client = FakeClient([text("Locking up."), tool("run_routine", routine="lockdown")],
                        [text("Done, **the house** is locked down.")])
    ai = Assistant(home, client=client)
    assert ai.ask("lock everything up for the night", room="kitchen") == \
        "Done, the house is locked down."
    assert {"node": "front", "channel": "lock", "state": True} in Recorder.calls
    # second request carried the tool result back
    last = client.requests[1]["messages"][-1]["content"][0]
    assert last["type"] == "tool_result" and "routine lockdown" in last["content"]


def test_request_shape(home):
    client = FakeClient([text("Hi.")])
    Assistant(home, client=client).ask("hello", room="bedroom")
    r = client.requests[0]
    assert r["model"] == "claude-opus-5-5"
    assert r["fallbacks"] == "default" and r["betas"] == ["server-side-fallback-2026-07-01"]
    assert r["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "room_lights_on" in r["system"][0]["text"] and "Portland" in r["system"][0]["text"]
    names = {t["name"] for t in r["tools"]}
    assert {"run_command", "home_status", "run_routine", "set_door", "web_search", "agenda", "lists",
            "memory", "announce", "schedule_command", "set_mode", "countdowns", "check_closed",
            "customize"} <= names
    assert "look_at_camera" not in names  # only offered when cameras are set up
    assert all(t.get("strict") for t in r["tools"] if t["name"] != "web_search")
    first = r["messages"][0]["content"]
    assert "speaking from: bedroom" in first[0]["text"] and first[1]["text"] == "hello"
    assert "house mode: home" in first[0]["text"]


def test_cannot_unlock_by_default(home):
    ai = Assistant(home, client=FakeClient([text("ok")]))
    system, tools = ai.profile(home.instances.main)
    door_tool = next(t for t in tools if t["name"] == "set_door")
    assert door_tool["input_schema"]["properties"]["state"]["enum"] == ["locked"]
    with pytest.raises(PermissionError):
        ai._run_tool("set_door", {"door": "front", "state": "unlocked"}, None)
    assert "cannot unlock" in system


def test_command_with_slots_and_here(home):
    client = FakeClient(
        [tool("run_command", command="room_lights_on", slots=[{"name": "room", "value": "Bedroom"}]),
         tool("run_command", command="lights_here", slots=[])],
        [tool("run_command", command="room_lights_on", slots=[{"name": "room", "value": "garage"}])],
        [text("The bedroom and kitchen lights are on. There's no garage.")])
    ai = Assistant(home, client=client)
    ai.ask("lights on here and in the bedroom and garage", room="kitchen")
    assert Recorder.calls == [{"node": "bedroom", "channel": "lights", "state": True},
                              {"node": "kitchen", "channel": "lights", "state": True}]
    err = client.requests[2]["messages"][-1]["content"][0]
    assert err["is_error"] and "garage isn't a valid room" in err["content"]


def test_remembers_conversation_per_room(home):
    client = FakeClient([text("Hi Sam.")], [text("You're Sam.")], [text("Hello.")])
    ai = Assistant(home, client=client)
    ai.ask("I'm Sam", room="kitchen")
    ai.ask("who am I?", room="kitchen")
    ai.ask("hi", room="bedroom")
    assert len(client.requests[1]["messages"]) == 3  # user, assistant, user
    assert len(client.requests[2]["messages"]) == 1  # bedroom is a separate conversation


def test_addressing(home):
    ai = Assistant(home, client=FakeClient())
    assert ai.addressed("hey house what's the weather") == "what's the weather"
    assert ai.addressed("Jarvis, lock up") == "lock up"
    assert ai.addressed("turn on the lights") is None
    assert ai.addressed("the house is cold") is None


def test_router_name_then_question(home):
    answered = []
    ai = Assistant(home, client=FakeClient())
    ai.ask = lambda q, room=None, person=None, on_text=None: answered.append((q, room)) or "Sunny."
    heard = iter(["house", "what's the weather"])
    router = AssistantRouter(ai, transcribe=lambda pcm: next(heard))
    assert router.handle("mic1", "kitchen", "house", b"\0\0", None)  # name alone
    assert home.spoken == ["listening"]
    assert router.handle("mic1", "kitchen", "unk unk unk", b"\0\0", None)  # the follow-up
    for t in list(__import__("threading").enumerate()):
        if t.name != "MainThread" and t.daemon and t.is_alive() and "_answer" in str(t._target):
            t.join(2)
    assert answered == [("what's the weather", "kitchen")]
    assert home.spoken[-1] == "Sunny."
    # continuous conversation: right after an answer, a follow-up needs no wake name
    assert "mic1" in router._follow_up
    router._follow_up.clear()                                          # ...until the window closes
    assert not router.handle("mic1", "kitchen", "lights on", b"", None)  # normal command


def test_spoken_cleanup():
    assert spoken("**Yes!** See [the forecast](https://x.y) at https://a.b") == "Yes! See the forecast at"
