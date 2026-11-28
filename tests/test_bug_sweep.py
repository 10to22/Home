"""Bugs found by running the whole house in the simulator and typing everything at it."""

import re

import pytest

from phrasehome.web import WebServer
from tests.test_house_life import home  # noqa: F401  (fixture: four housemates, house life on)


def web_for(home):
    w = WebServer.__new__(WebServer)
    w.home = home
    return w


class BrokenAI:
    """An assistant with no API key: what the SDK raises when it's asked anything."""
    names = ["house"]

    def addressed(self, text, room=None):
        words = text.lower().replace(",", "").split()
        return " ".join(words[1:]) if words and words[0] == "house" else None

    def ask(self, question, room=None, **kw):
        raise TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token, '
                        'or credentials to be set."')


def test_typed_house_commands_go_to_the_house_not_the_ai(home):
    home.assistant = BrokenAI()
    web = web_for(home)
    assert web.heard("house, parker's a vegetarian", "kitchen")["matched"] == "house life"
    assert "vegetarian" in home.life.people.about(home.instances.all["parker"]).lower()
    assert web.heard("house lockdown", "kitchen")["matched"] == "lockdown"  # a command with the name in front
    for said in ("house points", "house, what do you know about parker", "house quiz"):
        out = web.heard(said, "kitchen")
        assert out["matched"] and "error" not in out, said  # handled by the house itself, no AI needed


def test_typed_without_any_ai_still_takes_the_name_off(home):
    home.assistant = None
    web = web_for(home)
    assert web.heard("house, parker's a vegetarian", "kitchen")["matched"] == "house life"
    out = web.heard("house, write a sonnet about the fridge", "kitchen")
    assert out["matched"] is None and "assistant" in out["hint"].lower()


def test_no_ai_key_gives_a_plain_answer_not_the_sdk_error(home):
    home.assistant = BrokenAI()
    out = web_for(home).heard("house, write a sonnet about the fridge", "kitchen")
    assert out["assistant"] is None and "ANTHROPIC_API_KEY" in out["error"]
    assert "Could not resolve" not in out["error"]
    assert "assistant_error" in [k for k, _ in home.said]


def test_ask_steps_in_routines_carry_on_without_the_ai(home):
    home.assistant = BrokenAI()
    out = home.run_action({"backend": "assistant", "ask": "Morning briefing please"})
    assert "ANTHROPIC_API_KEY" in out and "assistant_error" in [k for k, _ in home.said]


def test_what_time_is_it_says_the_time(home):
    out = home.run_action({"backend": "voice", "speak": "time_now"})
    assert "{time}" not in out and re.search(r"\d", out)
    out = home.run_action({"backend": "voice", "speak": "date_now"})
    assert "{date}" not in out


@pytest.mark.parametrize("name,said", [("Sam's keys", "the tag on Sam's keys"), ("keys", "the tag on the keys"),
                                       ("my wallet", "the tag on my wallet")])
def test_tag_names_read_naturally(home, name, said):
    from phrasehome.rooms import Locator

    loc = Locator.__new__(Locator)
    loc.tags, loc.last = {"t1": {"name": name}}, {}
    out = loc.say_where(name)
    assert said in out and "'s's" not in out and "s's tag" not in out


def test_config_check_finds_modules_that_dont_exist(tmp_path):
    from phrasehome.config import load_config
    from phrasehome.doctor import node_problems

    (tmp_path / "c.yaml").write_text("""
settings: { default_backend: esp }
backends:
  esp: { type: esp32, nodes: { kitchen: k.local, living room: l.local } }
  phone: { type: log }
routines:
  movie: [ { node: living, channel: lamp, level: 20 }, { node: "{here}", channel: lights, state: off },
           { node: all, channel: lights, state: off }, { backend: phone, node: whatever, message: hi } ]
windows:
  lounge window: { node: living room, input: window }
  kitchen window: { node: kitchn, input: window }
commands:
  - { name: lamp, phrases: [lamp on], action: { node: kitchen, channel: lamp, state: on } }
""")
    bad = node_problems(load_config(tmp_path / "c.yaml"))
    assert len(bad) == 2
    assert any("routines.movie[0]" in b and "'living'" in b for b in bad)
    assert any("kitchen window" in b and "'kitchn'" in b for b in bad)


def test_the_example_config_only_names_real_modules():
    from pathlib import Path

    from phrasehome.config import load_config
    from phrasehome.doctor import node_problems

    root = Path(__file__).resolve().parents[1]
    for name in ("config.smarthome.example.yaml", "config.esp32.example.yaml", "config.example.yaml"):
        assert node_problems(load_config(root / name)) == [], name


# ---------------------------------------------------------------- second run of the simulator
def test_big_numbers_are_spelled_not_crashed():
    from phrasehome.clock import number_words
    from phrasehome.understanding_plus import spell_numbers, words_to_number

    assert spell_numbers("my essay is 2000 words") == "my essay is two thousand words"
    for n in (1000, 1500, 12345, 99999):
        assert words_to_number(number_words(n)) == n


def test_lines_that_say_in_the_room_get_the_room(home):
    out = home.voice.speak("meeting_when", room="living room", when="tomorrow at 7")
    assert "{room}" not in out and "living room" in out
    msg = home.run_action({"backend": "voice", "speak": "Smoke alarm ({room})"}, {"here": "kitchen"})
    assert "kitchen" in msg
    home.config.routines["fire_test"] = [{"backend": "voice", "speak": "Smoke alarm ({room})"}]
    home.run_action({"backend": "routine", "routine": "fire_test"})  # from the dashboard: no room at all
    assert "{room}" not in home.said[-1][1]


def test_stopping_the_shower_timer_straight_away(home):
    w = home.life.watch
    w.shower_start()
    w.shower_stop()
    assert home.said[-1][0] == "shower_stopped"


def test_door_status_at_the_hub_asks_which_door(home):
    assert home.life.quick("set my door to busy", room=None)
    assert home.said[-1][0] == "door_status_which"
