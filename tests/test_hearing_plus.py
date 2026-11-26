"""Accent fixes, room noise, echo cancelling, mic health, direction, the TV, and the family speech test."""

import json
import time
import types

import numpy as np

from phrasehome.hearing_plus import (AccentFixes, Direction, EchoCanceller, FarEnd, MicHealth, RoomNoise,
                                     export_dataset, keep_clip, needs_name, tv_on)
from phrasehome.listener import handle_result
from tests.test_assistant import home  # noqa: F401  (fixture)
from tests.test_wordbank import _matcher


def tone(seconds, level=3000, freq=300):
    t = np.arange(int(16000 * seconds)) / 16000
    return (level * np.sin(2 * np.pi * freq * t)).astype(np.int16).tobytes()


def test_accent_fixes_learn_after_twice(home):
    fx = AccentFixes(home.config)
    assert fx.differences("turn on the kitten lights", "turn on the kitchen lights") == [("kitten", "kitchen")]
    fx.learn("turn on the kitten lights", "turn on the kitchen lights")
    assert fx.apply("kitten lights off") == "kitten lights off"  # once isn't enough
    fx.learn("kitten lights on", "kitchen lights on")
    assert fx.apply("kitten lights off") == "kitchen lights off"
    home.config.extra["hearing"] = {"accent_fixes": False}
    assert AccentFixes(home.config).apply("kitten lights off") == "kitten lights off"


def test_teaching_on_the_history_page_teaches_the_accent(home):
    from phrasehome.understanding import Aliases

    a = Aliases(home.config)
    a.add("turn on the kitten lights", "room_lights_on", {"room": "kitchen"})
    item = a.add("turn on the kitten light", "room_lights_on", {"room": "kitchen"})
    assert item["meant"] == "turn on the kitchen lights"
    assert AccentFixes(home.config).rules() == {"kitten": "kitchen"}


def test_clips_and_dataset_export(home, tmp_path):
    from phrasehome.understanding import Aliases

    assert keep_clip(home.config, "abc", tone(0.2)) is None  # off by default
    home.config.extra["hearing"] = {"keep_clips": True}
    assert keep_clip(home.config, "abc", tone(0.2)).exists()
    Aliases(home.config).add("turn on the kitten lights", "room_lights_on", {"room": "kitchen"})
    out, n = export_dataset(home.config, [{"id": "abc", "heard": "turn on the kitten lights"},
                                          {"id": "nope", "heard": "something"}], tmp_path / "ds")
    assert n == 1
    assert json.loads((out / "metadata.jsonl").read_text()) == {"file_name": "abc.wav",
                                                               "text": "turn on the kitchen lights"}


def test_room_noise_learns_and_takes_out_the_hum(home):
    rn = RoomNoise(home.config, "kitchen")
    hum = tone(3, level=60, freq=120)
    out = rn(hum)
    assert rn.profile is not None and rn.quiet_frames > 50
    later = rn(tone(1, level=60, freq=120))
    assert np.abs(np.frombuffer(later, np.int16)).mean() < np.abs(np.frombuffer(hum, np.int16)).mean() / 2
    speech = rn(tone(1, level=8000, freq=400))
    assert np.abs(np.frombuffer(speech, np.int16)).mean() > 2000  # a voice gets through
    rn.save()
    assert RoomNoise(home.config, "kitchen").profile is not None
    assert len(out) > 0


def test_echo_canceller_gets_what_the_speaker_played():
    fe = FarEnd()
    fe.playing(tone(1), start=100.0)
    w = fe.window(100.5, 1600)
    assert np.abs(w).max() > 0 and len(w) == 1600
    assert not fe.window(200.0, 1600).any()

    class Spx:
        class EchoCanceller:
            @staticmethod
            def create(frame, tail, rate):
                return types.SimpleNamespace(process=lambda near, far: bytes(len(near)))

    ec = EchoCanceller(module=Spx)
    import phrasehome.hearing_plus as hp

    hp.far_end.playing(tone(1), start=time.monotonic() - 0.5)
    out = ec(tone(0.1))
    assert len(out) % 512 == 0 and not any(out)  # cancelled (by the fake)


def test_mic_health():
    mh = MicHealth(silent_minutes=1, offline_seconds=30)
    for t in (0.0, 25.0, 50.0, 65.0):
        mh.feed("sat1", "kitchen", bytes(3200), now=t)
    assert mh.problems(now=10) == {}
    assert "silence" in mh.problems(now=70)["sat1"]
    mh.feed("sat1", "kitchen", tone(0.2), now=80.0)
    assert mh.problems(now=81) == {}
    assert "stopped" in mh.problems(now=200)["sat1"]
    loud = (np.ones(16000 * 31) * 32767).astype(np.int16).tobytes()
    mh.feed("sat2", "hall", loud, now=300.0)
    assert "clipping" in mh.problems(now=301)["sat2"]
    assert {s["source"] for s in mh.status()} == {"sat1", "sat2"}


def test_direction_of_the_tv(home):
    home.config.extra["hearing"] = {"ignore_directions": {"living room": [[60, 120]]}}
    reader = types.SimpleNamespace(angle=lambda: 95)
    d = Direction(home.config, reader=reader)
    d.feed(tone(0.2))
    assert d.from_ignored("living room")
    reader.angle = lambda: 200
    d.feed(tone(0.2))
    assert not d.from_ignored("living room")
    assert not d.from_ignored("kitchen")


def test_tv_on_means_strangers_need_the_name(home, monkeypatch):
    import phrasehome.listener as listener

    home.config.extra["hearing"] = {"tv": {"kitchen": {"plug": "tv", "watts": 30}}}
    home.energy = types.SimpleNamespace(plugs={"tv": types.SimpleNamespace(power=90.0)})
    assert tv_on(home, "kitchen") and not tv_on(home, "bedroom")
    assert needs_name(home, "kitchen", None) and needs_name(home, "kitchen", "sam") is None
    ran = []
    monkeypatch.setattr(listener, "run_or_confirm", lambda m, d, c: ran.append(m.command.name))
    m = _matcher(home.config)
    result = {"text": "turn on the kitchen lights", "result": []}
    handle_result(result, m, home, home.config, room="kitchen", needs_name=needs_name(home, "kitchen", None))
    assert ran == []
    home.energy.plugs["tv"].power = 2.0  # TV off
    handle_result(result, m, home, home.config, room="kitchen", needs_name=needs_name(home, "kitchen", None))
    assert ran == ["room_lights_on"]


def test_family_speech_test(home):
    from phrasehome import family_test, speechtest

    items = [{"expected": "lockdown", "command": "lockdown"},
             {"expected": "turn on the kitchen lights", "command": "room_lights_on"}]
    for person, heard in (("sam", {0: "lockdown", 1: "turn on the kitchen lights"}),
                          ("ana", {0: "lock town", 1: "turn on the kitchen lights"})):
        speechtest.save_set(home.config, items, lambda it, i: bytes([i]) * 3200, person=person)
    hear = {"fake": lambda pcm: {1: "lockdown", 2: "turn on the kitchen lights"}[pcm[0]]}
    report = family_test.score_everyone(home.config, hear, show=lambda *a: None)
    assert report["sam"]["commands_right"] == 2 and report["sam"]["missed_commands"] == []
    assert family_test.people(home.config) == ["ana", "sam"]
