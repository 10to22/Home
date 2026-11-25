"""phrasehome speech-test: picking phrases, saving clips, scoring recognizers."""

import json

import numpy as np
import pytest

from phrasehome import speechtest
from phrasehome.config import load_config
from tests.test_understanding import CFG


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "c.yaml").write_text(CFG + "assistant: { names: [house] }\n")
    c = load_config(tmp_path / "c.yaml")
    c.data_dir = str(tmp_path / "data")
    return c


def test_word_error_rate():
    wer = speechtest.word_error_rate
    assert wer("lights on", "lights on") == 0 and wer("lights on", "Lights on.") == 0
    assert wer("turn on the lights", "turn on the light") == 0.25
    assert wer("lights on", "") == 1.0 and wer("lights on", "light son") == 1.0


def test_choose_phrases_mixes_commands_and_questions(cfg):
    items = speechtest.choose_phrases(cfg, 6, seed=1)
    assert len(items) == 6 and items[-1]["command"] == "(ai)" and items[-1]["expected"].startswith("house ")
    assert all(i["command"] in {c.name for c in cfg.commands} | {"(ai)"} for i in items)


def test_score_compares_recognizers(cfg):
    items = [{"expected": "lights on", "command": "lights_on"}, {"expected": "make coffee", "command": "coffee"},
             {"expected": "house what's the weather tomorrow", "command": "(ai)"}]
    tone = (np.sin(np.arange(16000) / 5) * 3000).astype(np.int16).tobytes()
    out = speechtest.save_set(cfg, items, lambda item, i: tone)
    assert json.loads((out / "manifest.json").read_text())[0]["file"] == "01.wav"
    perfect = iter(["lights on", "make coffee", "house what's the weather tomorrow"])
    sloppy = iter(["like sun", "make coffee", ""])
    res = speechtest.score(cfg, {"good": lambda pcm: next(perfect), "bad": lambda pcm: next(sloppy)}, show=lambda s: None)
    assert res["good"]["word_accuracy"] == 100 and res["good"]["commands_right"] == 3
    assert res["bad"]["commands_right"] == 1 and res["bad"]["word_accuracy"] < 50
    assert (out / "last-score.json").exists()


def test_score_without_clips_says_what_to_do(cfg):
    with pytest.raises(SystemExit, match="--record"):
        speechtest.score(cfg, {})
