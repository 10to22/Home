"""Better hearing: sentence detection, noise cleanup, Whisper (faked), the AI's name, live words."""

import json
import types

import numpy as np
import pytest

from phrasehome import hearing
from phrasehome.hearing import RATE, StreamCleaner, Vad, WakeSpotter, clean, house_words, sounds_like_name


def tone(seconds, freq=220.0, amp=0.3, noise=0.0, seed=0):
    t = np.arange(int(RATE * seconds)) / RATE
    x = amp * np.sin(2 * np.pi * freq * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t))  # syllable-ish
    if noise:
        x = x + np.random.default_rng(seed).normal(0, noise, len(x))
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def quiet(seconds, level=0.002, seed=1):
    return (np.random.default_rng(seed).normal(0, level, int(RATE * seconds)) * 32767).astype(np.int16).tobytes()


def feed_in_chunks(vad, pcm, chunk=4000 * 2):
    out = []
    for i in range(0, len(pcm), chunk):
        out += vad.feed(pcm[i:i + chunk])
    return out


def test_vad_finds_one_sentence_with_its_start():
    vad = Vad(end_silence=0.6)
    vad._web = None  # the energy detector (webrtcvad may not be installed)
    clips = feed_in_chunks(vad, quiet(1.0) + tone(1.2) + quiet(1.0))
    assert len(clips) == 1
    secs = len(clips[0]) / 2 / RATE
    assert 1.2 <= secs <= 2.3  # the sentence, a moment before it, and the quiet that ended it


def test_vad_two_sentences_and_ignores_a_click():
    vad = Vad(end_silence=0.5)
    vad._web = None
    pcm = quiet(0.8) + tone(0.8) + quiet(0.9) + tone(0.06) + quiet(0.9) + tone(0.7, 300) + quiet(0.9)
    assert len(feed_in_chunks(vad, pcm)) == 2  # the 60 ms click isn't a sentence


def test_clean_turns_background_down_and_levels_volume():
    noisy = quiet(1.0, level=0.01) + tone(1.0, amp=0.05, noise=0.01) + quiet(1.0, level=0.01)
    out = np.frombuffer(clean(noisy), dtype=np.int16).astype(float)
    x = np.frombuffer(noisy, dtype=np.int16).astype(float)
    n = RATE
    ratio_in = np.std(x[n:2 * n]) / np.std(x[:n])
    ratio_out = np.std(out[n:2 * n]) / np.std(out[:n])
    assert len(out) == len(x)
    assert ratio_out > ratio_in * 1.5  # speech stands out more against the background
    assert np.std(out[n:2 * n]) > np.std(x[n:2 * n]) * 2  # a quiet voice is brought up


def test_stream_cleaner_brings_a_quiet_mic_up():
    sc = StreamCleaner()
    pcm = tone(2.0, amp=0.01)
    out = b"".join(sc(pcm[i:i + 8000]) for i in range(0, len(pcm), 8000))
    assert np.std(np.frombuffer(out[-8000:], np.int16)) > np.std(np.frombuffer(pcm[-8000:], np.int16)) * 2


def test_house_words_prime_whisper(tmp_path):
    from phrasehome.config import load_config
    from tests.test_freak import CONFIG

    (tmp_path / "c.yaml").write_text(CONFIG)
    words = house_words(load_config(tmp_path / "c.yaml"), ["house"])
    assert words.startswith("house, Household") and "Finn" in words and "freak mode" in words
    assert len(house_words(load_config(tmp_path / "c.yaml"), ["house"], limit=40)) <= 41


class FakeModel:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        return iter(self.segments), None


def seg(text, words, no_speech=0.01, logprob=-0.2):
    return types.SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob,
                                 words=[types.SimpleNamespace(word=w, probability=p) for w, p in words])


def test_whisper_text_confidence_and_prompt():
    w = hearing.Whisper.__new__(hearing.Whisper)
    w.model = FakeModel([seg(" Freak mode.", [(" Freak", 0.9), (" mode.", 0.7)])])
    text, conf, words = w.transcribe(tone(1.0), prompt="Finn, freak mode.", language="en")
    assert text == "Freak mode." and conf == pytest.approx(0.8) and words[0] == {"word": "Freak", "conf": 0.9}
    assert w.model.calls[0]["initial_prompt"] == "Finn, freak mode." and w.model.calls[0]["language"] == "en"


def test_whisper_doesnt_hear_things_in_silence():
    w = hearing.Whisper.__new__(hearing.Whisper)
    w.model = FakeModel([seg(" Thank you.", [(" Thank", 0.4), (" you.", 0.3)])])
    assert w.transcribe(quiet(1.0))[0] == ""
    w.model = FakeModel([seg(" Lights on.", [(" Lights", 0.9)], no_speech=0.9)])
    assert w.transcribe(quiet(1.0))[0] == ""


def test_wake_spotter_and_sound_alikes():
    class Rec:
        def __init__(self, model, rate, grammar):
            assert json.loads(grammar) == ["house", "[unk]"]
            self.out = None

        def SetWords(self, on):
            pass

        def AcceptWaveform(self, pcm):
            return self.out is not None

        def Result(self):
            return json.dumps(self.out)

    sp = WakeSpotter(types.SimpleNamespace(KaldiRecognizer=Rec), None, ["house"])
    assert not sp.feed(b"..", 1.0)
    sp.rec.out = {"result": [{"word": "house", "conf": 0.95}, {"word": "[unk]", "conf": 0.5}]}
    assert sp.feed(b"..", 2.0) and sp.heard_recently(3.0) and not sp.heard_recently(30.0)
    sp.rec.out = {"result": [{"word": "house", "conf": 0.5}]}  # unsure: no
    assert not sp.feed(b"..", 40.0)
    assert sounds_like_name("How's", ["house"]) and sounds_like_name("mouse", ["house"])
    assert not sounds_like_name("lights", ["house"])


def test_misheard_name_still_reaches_the_ai():
    from phrasehome.assistant import AssistantRouter

    asked = []
    a = types.SimpleNamespace(addressed=lambda t, r: None, min_confidence=0.55, follow_up_seconds=8)
    r = AssistantRouter.__new__(AssistantRouter)
    r.assistant, r.transcribe, r._follow_up = a, None, {}
    r._answer = lambda q, *rest: asked.append(q)
    import threading

    real = threading.Thread
    threading.Thread = lambda target, args, daemon: types.SimpleNamespace(start=lambda: target(*args))
    try:
        assert r.handle("local", "kitchen", "How's the weather tomorrow", b"", None, confidence=0.9, woke=True)
        assert not r.handle("local", "kitchen", "how's the weather tomorrow", b"", None, confidence=0.9, woke=False)
    finally:
        threading.Thread = real
    assert asked == ["the weather tomorrow"]


def test_whisper_sentence_goes_to_commands_and_live_line(tmp_path):
    from phrasehome.config import load_config
    from phrasehome.listener import Engine
    from tests.test_hush import CFG

    (tmp_path / "c.yaml").write_text(CFG.replace("settings: { default_backend: dev }",
                                                 "settings: { default_backend: dev, log_heard: true }"))
    cfg = load_config(tmp_path / "c.yaml")
    lives, submitted = [], []
    e = Engine.__new__(Engine)
    e.config, e.router, e.spk, e.spotters, e._live, e._names, e.prompt = cfg, None, None, {}, {}, [], "p"
    e.whisper = types.SimpleNamespace(transcribe=lambda audio, prompt, lang: (
        "Lights on.", 0.92, [{"word": "Lights", "conf": 0.95}, {"word": "on.", "conf": 0.89}]))
    from phrasehome.matcher import Matcher, expand

    s = cfg.settings
    e._lang = lambda room: types.SimpleNamespace(lang="en", model=None, matcher=Matcher(
        expand(cfg.commands, cfg.slots), s.match_mode, s.match_threshold, s.block_words))
    e.dispatcher = types.SimpleNamespace(
        state=types.SimpleNamespace(is_private=lambda r: False),
        events=types.SimpleNamespace(live=lambda kind, **d: lives.append((kind, d))),
        submit=lambda m: submitted.append(m) or True)
    e._handle_clip("local", "kitchen", tone(1.0), None)
    assert submitted and submitted[0].command.name == "lights_on" and submitted[0].room == "kitchen"
    kind, d = lives[0]
    assert kind == "heard" and d["words"][0] == {"word": "Lights", "conf": 0.95} and d["confidence"] == 0.92
