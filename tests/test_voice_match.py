"""Ranking installed voices by how close they sound to a recording."""

import shutil
import wave

import numpy as np
import pytest

from phrasehome.voice_match import RATE, Profile, distance, parse_say_list, pitch_track, profile


def tone_speech(f0, wobble_st, seconds=4.0, syll=5.0):
    """A fake 'voice': harmonics at f0 with some pitch movement, pulsed like syllables."""
    t = np.arange(int(RATE * seconds)) / RATE
    f = f0 * 2 ** (wobble_st * np.sin(2 * np.pi * 0.7 * t) / 12)
    phase = 2 * np.pi * np.cumsum(f) / RATE
    x = sum(np.sin(k * phase) / k for k in range(1, 6))
    env = 0.5 + 0.5 * np.sin(2 * np.pi * syll * t) ** 2
    return (0.2 * x * env).astype(np.float32)


@pytest.mark.parametrize("f0", [110, 180, 250])
def test_pitch_track_finds_pitch(f0):
    p, _ = pitch_track(tone_speech(f0, 0.0))
    assert abs(np.median(p[p > 0]) - f0) / f0 < 0.03


def test_profile_tells_flat_from_lively():
    flat, lively = profile(tone_speech(200, 0.3)), profile(tone_speech(200, 4.0))
    assert flat.spread_st < 1.0 < 2.0 < lively.spread_st
    assert abs(flat.pitch_hz - 200) < 10


def test_distance_ranks_closer_voices_first():
    ref = Profile(150, 1.9, 1230, 4.8)
    near, far = Profile(158, 1.5, 1300, 5.0), Profile(240, 4.5, 2000, 6.5)
    assert distance(ref, ref) == 0
    assert distance(ref, near) < distance(ref, far)


def test_parse_say_list():
    out = """Albert              en_US    # Hello! My name is Albert.
Amélie              fr_CA    # Bonjour! Je m’appelle Amélie.
Eddy (English (UK)) en_GB    # Hello! My name is Eddy.
Karen               en_AU    # Hi my name is Karen
Samantha            en_US    # Hello! My name is Samantha.
"""
    assert parse_say_list(out) == ["Albert", "Eddy (English (UK))", "Karen", "Samantha"]


@pytest.mark.skipif(not (shutil.which("espeak-ng") and shutil.which("ffmpeg")), reason="needs espeak-ng and ffmpeg")
def test_match_ranks_the_voice_that_made_the_recording_first(tmp_path):
    from phrasehome.config import load_config
    from phrasehome.voice import Voice
    from phrasehome.voice_match import TEST_TEXT, match

    (tmp_path / "c.yaml").write_text(f"""
data_dir: {tmp_path}/data
backends: {{ dev: {{ type: log }} }}
settings: {{ default_backend: dev }}
voice: {{ engine: espeak }}
commands:
  - {{ phrases: [hello], action: {{ backend: dev }} }}
""")
    cfg = load_config(tmp_path / "c.yaml")
    v = Voice(cfg)
    # a "recording" by one of the voices, saying something different from the test sentence
    rec = v.synthesize("Good evening. The oven has been preheated for an hour, which I assume was on "
                       "purpose. Dinner, apparently, is a concept.", {"engine": "espeak",
                                                                      "espeak_voice": "en+f3", "speed": 1.0})
    with wave.open(str(rec)) as w:
        assert w.getnframes() > 0
    _, best = match(cfg, rec, top=5, progress=lambda m: None)
    labels = [(b[1], b[2]) for b in best]
    assert ("en+f3", None) in labels
    assert len(set(labels)) == len(labels)


def test_mac_joke_voices_are_skipped_unless_asked(monkeypatch):
    import phrasehome.voice_match as vm

    listing = """Bahh                en_US    # Do not pull the wool over my eyes.
Samantha            en_US    # Hello! My name is Samantha.
Zarvox              en_US    # That looks like a peaceful planet.
"""
    monkeypatch.setattr(vm.sys, "platform", "darwin")
    monkeypatch.setattr(vm.shutil, "which", lambda name: "/usr/bin/say")
    monkeypatch.setattr(vm.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": listing})())
    assert vm.say_voices() == ["Samantha"]
    assert vm.say_voices(novelty=True) == ["Bahh", "Samantha", "Zarvox"]


@pytest.mark.parametrize("style", ["mean", "deadpan", "plain"])
def test_demo_lines_have_every_blank_filled(tmp_path, style):
    from phrasehome.config import load_config
    from phrasehome.voice import Voice

    (tmp_path / "c.yaml").write_text(f"""
data_dir: {tmp_path}/data
backends: {{ dev: {{ type: log }} }}
settings: {{ default_backend: dev }}
voice: {{ engine: none, style: {style} }}
commands:
  - {{ phrases: [hello], action: {{ backend: dev }} }}
""")
    lines = Voice(load_config(tmp_path / "c.yaml")).demo_lines()
    assert len(lines) >= 30
    assert not [t for _, t in lines if "{" in t]


@pytest.mark.skipif(not (shutil.which("espeak-ng") and shutil.which("ffmpeg")), reason="needs espeak-ng and ffmpeg")
def test_demo_saves_one_file(tmp_path):
    from phrasehome.config import load_config
    from phrasehome.voice import Voice

    (tmp_path / "c.yaml").write_text(f"""
data_dir: {tmp_path}/data
backends: {{ dev: {{ type: log }} }}
settings: {{ default_backend: dev }}
voice: {{ engine: espeak, effect: deep }}
commands:
  - {{ phrases: [hello], action: {{ backend: dev }} }}
""")
    out = Voice(load_config(tmp_path / "c.yaml")).demo(tmp_path / "demo.wav", every_variant=False, show=lambda s: None)
    with wave.open(str(out)) as w:
        assert w.getnframes() / w.getframerate() > 60  # over a minute of speech
