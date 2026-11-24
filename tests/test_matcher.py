from pathlib import Path

import pytest

from phrasehome.actions import render
from phrasehome.config import load_config
from phrasehome.matcher import Matcher, expand, normalize, vocabulary

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cfg():
    return load_config(ROOT / "config.example.yaml")


def make(cfg, mode="utterance"):
    s = cfg.settings
    return Matcher(expand(cfg.commands, cfg.slots), mode, s.match_threshold, s.block_words)


def test_normalize():
    assert normalize("Turn ON the Lights!") == "turn on the lights"
    assert normalize("[unk] lights on") == "unk lights on"


def test_exact_and_variants(cfg):
    m = make(cfg)
    assert m.match("turn on the lights").command.name == "lights_on"
    assert m.match("lights off").command.name == "lights_off"
    assert m.match("turn the lights on").command.name == "lights_on"


def test_slots_and_render(cfg):
    m = make(cfg).match("turn on the living room lights")
    assert m.command.name == "room_lights_on"
    assert m.slots == {"room": "living room"}
    assert render(m.command.action, m.slots)["say"] == "turn on the living room lights"

    d = make(cfg).match("dim the lights to fifty percent")
    assert d.slots == {"level": "50"}
    assert render(d.command.action, d.slots)["say"] == "set the lights to 50 percent"


def test_fuzzy_small_mistake(cfg):
    assert make(cfg).match("turn on the light").command.name == "lights_on"


def test_unrelated_speech_ignored(cfg):
    m = make(cfg)
    for s in ["what's for dinner", "i was talking about the lights yesterday", "the", ""]:
        assert m.match(s) is None


def test_negation_blocked(cfg):
    assert make(cfg).match("don't turn on the lights") is None
    assert make(cfg, "contains").match("please don't turn on the lights") is None


def test_contains_mode(cfg):
    m = make(cfg, "contains")
    assert m.match("ok so turn on the lights please").command.name == "lights_on"
    assert make(cfg, "utterance").match("ok so turn on the lights please") is None


def test_prefers_more_specific(cfg):
    m = make(cfg, "contains").match("turn on the kitchen lights")
    assert m.command.name == "room_lights_on"


def test_vocabulary(cfg):
    v = vocabulary(expand(cfg.commands, cfg.slots))
    assert "turn on the lights" in v and "kitchen" in v and v[-1] == "[unk]"
