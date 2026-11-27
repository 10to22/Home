"""train-hearing: voices say your phrases, through mp3, the recognizer hears them, mishearings are learned."""

import shutil
import subprocess

import numpy as np
import pytest

from phrasehome import hearing_train as ht
from phrasehome.hearing_plus import AccentFixes
from phrasehome.understanding import Aliases
from tests.test_assistant import home  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")

ITEMS = [{"text": "turn on the kitchen lights", "command": "room_lights_on", "slots": {"room": "kitchen"}},
         {"text": "turn on the bedroom lights", "command": "room_lights_on", "slots": {"room": "bedroom"}},
         {"text": "lockdown", "command": "lockdown", "slots": {}},
         {"text": "lights on", "command": "lights_here", "slots": {}}]


class ToneVoice(ht.Voice):
    """Each phrase becomes a tone whose length says which phrase it was (so a fake recognizer can 'hear' it)."""

    table: list[str] = []

    def __init__(self, name):
        self.name = name

    def say(self, text, rate=1.0):
        if text not in self.table:
            self.table.append(text)
        secs = 0.5 + 0.25 * self.table.index(text)
        t = np.arange(int(ht.RATE * secs)) / ht.RATE
        return (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16).tobytes()


def fake_ear(mishear):
    def hear(pcm):
        secs = len(pcm) / 2 / ht.RATE - 0.7  # minus the padding
        i = int(round((secs - 0.5) / 0.25))
        text = ToneVoice.table[max(0, min(i, len(ToneVoice.table) - 1))]
        return mishear(text)
    return hear


def test_phrases_cover_every_command(home):
    items = ht.phrases(home.config, 10)
    assert {c.name for c in home.config.commands} <= {i["command"] for i in items}


def test_mp3_round_trip_keeps_the_audio():
    pcm = (np.sin(np.arange(16000) / 5) * 5000).astype(np.int16).tobytes()
    mp3, back = ht.mp3_round_trip(pcm, 48)
    assert mp3[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3") or mp3[0] == 0xFF
    assert abs(len(back) - len(pcm)) < 16000  # about the same length


def test_learns_only_what_makes_a_difference(home):
    ToneVoice.table = []

    def mishear(text):
        return text.replace("kitchen", "kitten").replace("lockdown", "lock town").replace("lights on", "lice on")

    voices = [ToneVoice("a"), ToneVoice("b"), ToneVoice("c")]  # "a" is held out: measured, never learned from
    r = ht.run(home.config, per_phrase=3, voices=voices, hear={"fake": fake_ear(mishear)}, show=lambda *a: None,
               items=ITEMS)
    fixes = r["learned"]["word_fixes"]
    assert fixes.get("lice") == "lights"  # "lice on" ran nothing; now it runs lights on
    assert "kitten" not in fixes  # the matching already got "turn on the kitten lights" right: nothing to prove
    assert any(a["heard"] == "lock town" and a["command"] == "lockdown" for a in Aliases(home.config).all())
    assert r["after"]["fake"]["accuracy"] > r["before"]["fake"]["accuracy"]
    h = r["after"]["fake"]["holdout"]
    assert h["voices"] == ["a"] and h["after"] > h["before"]  # and it helps a voice it never learned from


def test_never_rewrites_common_words(home):
    ToneVoice.table = []
    r = ht.run(home.config, per_phrase=3, voices=[ToneVoice("a"), ToneVoice("b"), ToneVoice("c")],
               hear={"fake": fake_ear(lambda t: t.replace("lights", "they"))}, show=lambda *a: None, items=ITEMS)
    assert "they" not in r["learned"]["word_fixes"]


def test_a_clash_is_reported_not_learned(home):
    ToneVoice.table = []

    def mishear(text):
        return "lockdown" if "bedroom" in text else text  # a light command heard as another command

    r = ht.run(home.config, per_phrase=2, voices=[ToneVoice("a"), ToneVoice("b")],
               hear={"fake": fake_ear(mishear)}, show=lambda *a: None, items=ITEMS)
    assert any(c[2] == "lockdown" for c in r["learned"]["clashes"])
    assert not any(a["heard"] == "lockdown" for a in Aliases(home.config).all())


def test_dry_run_learns_nothing(home):
    ToneVoice.table = []
    ht.run(home.config, per_phrase=2, voices=[ToneVoice("a"), ToneVoice("b")],
           hear={"fake": fake_ear(lambda t: t.replace("kitchen", "kitten"))}, dry=True, show=lambda *a: None, items=ITEMS)
    assert AccentFixes(home.config).rules() == {} and Aliases(home.config).all() == []


def test_background_audio_that_would_fire(home, tmp_path):
    f = tmp_path / "talk.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-af", "volume=0.5",
                    "-y", str(f)], check=True)
    out = ht.background(home.config, [f], {"fake": lambda pcm: "lockdown"}, show=lambda *a: None)
    assert out["fake"]["fired"] and out["fake"]["fired"][0]["command"] == "lockdown"
    quiet = ht.background(home.config, [f], {"fake": lambda pcm: "and then she said the weather was nice"}, show=lambda *a: None)
    assert quiet["fake"]["fired"] == []


def test_import_your_own_recordings(home, tmp_path):
    from phrasehome import speechtest

    memos = tmp_path / "memos"
    memos.mkdir()
    for name in ("turn on the kitchen lights", "lockdown"):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y",
                        str(memos / f"{name.replace(' ', '_')}.mp3")], check=True)
    (memos / "x1.m4a").write_bytes((memos / "lockdown.mp3").read_bytes())
    (memos / "transcripts.txt").write_text("x1.m4a|what's the weather tomorrow\n")
    out = speechtest.import_files(home.config, memos, show=lambda *a: None)
    import json

    items = {i["expected"]: i["command"] for i in json.loads((out / "manifest.json").read_text())}
    assert items["turn on the kitchen lights"] == "room_lights_on" and items["lockdown"] == "lockdown"
    assert items["what's the weather tomorrow"] == "(ai)"


def test_shipped_learnings_apply_only_to_commands_the_house_has(home, monkeypatch):
    monkeypatch.setattr(ht, "_SHIPPED", {"word_fixes": {"loq": "lockdown", "kitchen": "kitten"},
                                        "sentences": [{"heard": "lock dow", "command": "lockdown"},
                                                      {"heard": "start the party", "command": "not_here"}]})
    from phrasehome.matcher import Matcher
    from phrasehome.understanding import candidates_with_aliases

    m = Matcher(candidates_with_aliases(home.config))
    assert m.match("lock dow").command.name == "lockdown"
    rules = AccentFixes(home.config).rules()
    assert rules.get("loq") == "lockdown" and "kitchen" not in rules  # never rewrites a word the house uses


def test_keep_then_rescore(home):
    ToneVoice.table = []
    ht.run(home.config, per_phrase=1, voices=[ToneVoice("a")], hear={"fake": fake_ear(lambda t: t)},
           show=lambda *a: None, items=ITEMS, keep=True, dry=True)
    r = ht.run(home.config, hear={"fake": lambda pcm: "lockdown"}, show=lambda *a: None, rescore=True, dry=True)
    assert len(r["clips"]) == len(ITEMS) and r["before"]["fake"]["clips"] == len(ITEMS)
