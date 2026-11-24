"""Commands and schedules by voice, the new assistant tools, diary, watcher, welcome-back."""

import json

import numpy as np
import pytest

from phrasehome import ai
from phrasehome.assistant import Assistant
from phrasehome.config import load_config
from phrasehome.diary import Diary
from phrasehome.dispatcher import Dispatcher
from phrasehome.matcher import Matcher, expand
from phrasehome.meals import Meals
from phrasehome.rooms import RoomTracker
from phrasehome.vision import CameraHub
from tests.test_assistant import FakeClient, text, tool
from tests.test_door import Recorder
from tests.test_house import wait_for

CFG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none }
assistant: { names: [house] }
instances:
  household: { main: true, rooms: [living room, kitchen] }
  sam: { name: Sam, rooms: [bedroom], phone: sam-topic }
cameras:
  front door: { url: "http://x/capture", roles: [door] }
tags: { keys: { mac: "aa:00:00:00:00:01", name: "the keys" } }
slots:
  room: [kitchen, bedroom, living room]
routines:
  lights_low: [ { node: living room, channel: lamp, level: 20 } ]
commands:
  - { name: room_lights_on, phrases: ["turn on the {room} lights"], action: { node: "{room}", channel: lights, state: on } }
  - { name: lights_here, phrases: [lights on], action: { node: "{here}", channel: lights, state: on } }
"""


@pytest.fixture
def home(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.said = []
    h.voice.speak = lambda key, room=None, rooms=None, everywhere=False, urgent=False, **v: \
        h.said.append((key, room, everywhere)) or key
    yield h
    h.close()


def test_custom_command_by_voice(home):
    c = home.customs
    e = c.add_command("cozy", ["cozy time", "make it cozy"], [
        {"routine": "lights_low"}, {"command": "room_lights_on", "slots": [{"name": "room", "value": "Kitchen"}]},
        {"say": "Enjoy."}])
    assert e["name"] == "custom_cozy"
    m = Matcher(expand(home.config.commands, home.config.slots)).match("make it cozy")
    assert m.command.name == "custom_cozy"
    home.run_action(m.command.action)
    assert {"node": "living room", "channel": "lamp", "level": 20} in Recorder.calls
    assert {"node": "kitchen", "channel": "lights", "state": True} in Recorder.calls
    assert home.said[-1][0] == "Enjoy."
    with pytest.raises(ValueError, match="already a command"):
        c.add_command("again", ["lights on"], [{"say": "x"}])
    with pytest.raises(ValueError, match="no command called"):
        c.add_command("bad", ["do magic"], [{"command": "teleport"}])
    # survives a restart
    h2 = Dispatcher(load_config(home.config.base_dir / "c.yaml"))
    assert any(x.name == "custom_cozy" for x in h2.config.commands)
    h2.close()
    assert c.remove("cozy") == 1
    assert not any(x.name == "custom_cozy" for x in home.config.commands)


def test_custom_schedule_and_here(home):
    home.customs.add_schedule("morning lights", "07:00", "weekdays", [{"command": "lights_here"}])
    sc = next(s for s in home.config.schedules if s["name"] == "custom: morning lights")
    assert sc["do"][0]["node"] == "{here}" and sc["days"] == "weekdays"
    assert "morning lights at 07:00 (weekdays)" in home.customs.list()["schedules"][0]


def test_customize_tool_via_ai(home):
    client = FakeClient(
        [tool("customize", action="add_command", name="movie night", phrases=["movie night"], at="", days="",
              steps=[{"command": "", "routine": "lights_low", "slots": [], "say": ""}])],
        [text("Done: say 'movie night'.")])
    Assistant(home, client=client).ask("when I say movie night, dim the living room", room="living room")
    assert "added command custom_movie_night" in client.requests[1]["messages"][-1]["content"][0]["content"]


def test_look_at_camera_sends_the_picture(home):
    hub = CameraHub(home)
    hub.feeds["front door"].set_frame(np.zeros((60, 80, 3), np.uint8))
    client = FakeClient([tool("look_at_camera", camera="front door", question="")],
                        [text("A delivery driver with a box.")])
    ai_ = Assistant(home, client=client)
    assert "look_at_camera" in {t["name"] for t in ai_.profile(home.instances.main)[1]}
    assert ai_.ask("what's at the door?", room="kitchen") == "A delivery driver with a box."
    result = client.requests[1]["messages"][-1]["content"][0]
    assert result["content"][0]["type"] == "image" and result["content"][0]["source"]["media_type"] == "image/jpeg"


def test_assistant_meal_and_where_tools(home):
    Meals(home)
    RoomTracker(home).locator.update("kitchen", [{"mac": "aa:00:00:00:00:01", "rssi": -50}])
    client = FakeClient(
        [tool("meals", action="set", day="tue", slot="dinner", name="Tacos", ingredients=["1 lb ground beef", "8 tortillas"],
              request="", next_week=False),
         tool("where_is", what="keys")],
        [text("Tacos are on Tuesday, and your keys are in the kitchen.")])
    Assistant(home, client=client).ask("tacos on tuesday, and where are my keys", room="bedroom")
    results = [r["content"] for r in client.requests[1]["messages"][-1]["content"]]
    assert "shopping list updated" in results[0] and results[1] == "The keys are in the kitchen."
    assert "ground beef (1 lb)" in home.instances.list_get(home.instances.main, "shopping")


def test_diary_and_watcher(home, monkeypatch):
    d = Diary(home)
    home.events.log("doorbell", door="front")
    home.events.log("package.delivered", description="a box")
    prompts = []

    def fake(h, prompt, schema=None, **kw):
        prompts.append(prompt)
        if schema:
            return {"say_something": True, "message": "The front door has been unlocked for an hour.",
                    "urgent": False, "everyone": True}
        return "A quiet day. A box was delivered and someone rang the doorbell."
    monkeypatch.setattr(ai, "ask", fake)
    assert d.write("2026-11-24" if False else None).startswith("A quiet day")
    assert d.read() == "A quiet day. A box was delivered and someone rang the doorbell."
    assert '"package.delivered"' in prompts[0]
    res = d.watch()
    assert res["message"] in d._said
    assert home.events.query("watcher")


def test_welcome_back_summary(home, monkeypatch):
    d = Diary(home)
    monkeypatch.setattr(ai, "ask", lambda h, prompt, **kw: "While you were out, a package arrived.")
    home.state.set_presence("sam", "home")
    home.state.set_presence("sam", "away")              # everyone left
    home.events.log("package.delivered", description="a box")
    home.state.set_presence("sam", "home")              # first one home
    assert wait_for(lambda: any("package arrived" in k for k, *_ in home.said))
