"""The voice modeler: accents, a mood per line, tuning, the engines' knobs, and the LAN voice server."""

import json
import shutil
import subprocess
import sys
import threading
import urllib.request
import wave
from pathlib import Path

import pytest

from phrasehome import tts_neural, voice as vmod
from phrasehome.config import load_config
from phrasehome.voice import Voice
from phrasehome.voice_style import (EMOTIONS, SAFETY_KEYS, VoiceModeler, accent_name, guess, mood_name,
                                    split_tag, strip_tags)

CFG = """
backends: {{ dev: {{ type: record }} }}
settings: {{ default_backend: dev }}
voice: {voice}
commands:
  - {{ name: lights_on, phrases: [lights on], action: {{ node: kitchen, channel: lights, state: on }} }}
"""

# a pretend voice engine: writes a short tone, and what it was asked (the mood) next to it
TONE = ("import sys, wave, struct, math; w = wave.open(sys.argv[1], 'wb'); w.setnchannels(1); w.setsampwidth(2); "
        "w.setframerate(16000); w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(i / 8))) "
        "for i in range(1600))); w.close(); open(sys.argv[1] + '.mood', 'w').write(sys.argv[2])")


def make_cfg(tmp_path, voice: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "c.yaml").write_text(CFG.format(voice=voice))
    cfg = load_config(tmp_path / "c.yaml")
    cfg.data_dir = str(tmp_path / "data")
    return cfg


# ---------- tags and words ----------
def test_tags_are_read_and_never_spoken():
    assert split_tag("[Cheeky] Lights off. [annoyed] Again.") == ("cheeky", "Lights off. Again.")
    assert split_tag("<mood: happy> Good news!") == ("cheerful", "Good news!")
    assert split_tag("Keep it down (quiet) please") == (None, "Keep it down (quiet) please")
    assert split_tag("[1] is a citation, not a mood") == (None, "[1] is a citation, not a mood")
    assert strip_tags("[calm] It's okay.") == "It's okay."
    assert mood_name("Grumpy") == "annoyed" and mood_name("bogus") is None


def test_accent_names():
    for spelling in ("en_AU", "en-au", "AU", "aussie", "Australian"):
        assert accent_name(spelling) == "en_AU"
    assert accent_name("klingon") is None and accent_name(None) is None


def test_guess_from_the_words():
    assert guess("Warning: smoke in the kitchen. Get out now.") == "clear"
    assert guess("I'm sorry to hear that. Take care.") == "calm"
    assert guess("Seriously? Still in bed?") == "annoyed"
    assert guess("Great news, the parcel's here!") == "cheerful"
    assert guess("The kitchen lights are on.") is None


# ---------- picking a mood ----------
def test_moods_are_on_unless_turned_off(tmp_path):
    m = VoiceModeler(make_cfg(tmp_path, "{engine: none}"))
    assert m.enabled and m.pick("wake_nag", "Get up.") == "annoyed" and "[cheeky]" in m.prompt_hint()
    off = VoiceModeler(make_cfg(tmp_path / "off", "{engine: none, emotion: false}"))
    assert not off.enabled and off.pick("wake_nag", "Get up.", explicit="cheeky") is None
    assert off.prompt_hint() == ""


def test_safety_lines_are_always_clear(tmp_path):
    m = VoiceModeler(make_cfg(tmp_path, "{engine: none, emotion: true, emotions: {smoke_alarm: cheeky}}"))
    for key in SAFETY_KEYS:
        assert m.pick(key, "Smoke!", explicit="cheeky", phrase_mood="annoyed", tagged="cheerful", quiet=True) == "clear"
    assert not m.wants_effect("clear") and m.wants_effect("cheeky")


def test_what_wins_when_picking_a_mood(tmp_path):
    m = VoiceModeler(make_cfg(tmp_path, "{engine: none, emotion: true, style: aussie, emotions: {timer_done: annoyed}}"))
    assert m.pick("wake_nag", "x", explicit="calm", phrase_mood="cheerful", tagged="urgent") == "calm"
    assert m.pick("wake_nag", "x", phrase_mood="cheerful", tagged="urgent") == "cheerful"
    assert m.pick("wake_nag", "x", tagged="urgent") == "urgent"
    assert m.pick("timer_done", "x") == "annoyed"  # config.yaml's own mood for the key
    assert m.pick("wake_nag", "x") == "annoyed"  # the built-in mood of the key
    assert m.pick(None, "Happy birthday, mate!") == "cheerful"  # a guess from the words
    assert m.pick(None, "The lights are on.") == "cheeky"  # the Aussie style's usual mood
    assert m.pick(None, "The lights are on.", quiet=True) == "soft"  # quiet hours
    assert m.pick(None, "x", tagged="urgent", quiet=True) == "urgent"
    assert "[cheeky]" in m.prompt_hint()
    plain = VoiceModeler(make_cfg(tmp_path / "p", "{engine: none, emotion: true, style: plain}"))
    assert plain.pick(None, "The lights are on.") == "neutral"


# ---------- tuning ----------
def test_tuning_is_clamped_saved_and_reset(tmp_path):
    cfg = make_cfg(tmp_path, "{engine: none, emotion: true, emotion_strength: 1}")
    m = VoiceModeler(cfg)
    assert m.tune("Cheeky", pitch=40, rate=1.2)["pitch"] == 6.0
    with pytest.raises(KeyError):
        m.tune("cheeky", warble=1)
    with pytest.raises(KeyError):
        m.tune("furious", pitch=1)
    again = VoiceModeler(cfg)  # read back from data/voice_tuning.json
    assert again.knobs("cheeky")["pitch"] == 6.0 and again.knobs("cheeky")["rate"] == 1.2
    assert again.knobs("cheeky")["lively"] == EMOTIONS["cheeky"]["lively"]
    assert again.describe()["emotions"]["cheeky"]["tuned"] == ["pitch", "rate"]
    again.reset("cheeky")
    assert VoiceModeler(cfg).knobs("cheeky")["pitch"] == EMOTIONS["cheeky"]["pitch"]


def test_options_from_the_dashboard_win_over_config(tmp_path):
    cfg = make_cfg(tmp_path, "{engine: none, emotion: false}")
    m = VoiceModeler(cfg)
    m.set_options(enabled=True, accent="aussie")
    m2 = VoiceModeler(cfg)
    assert m2.enabled and m2.accent == "en_AU"
    m2.set_options(accent="config")
    assert VoiceModeler(cfg).accent is None
    with pytest.raises(KeyError):
        m2.set_options(accent="martian")
    m2.reset()
    assert not VoiceModeler(cfg).enabled


# ---------- what each engine is told ----------
def test_engine_params(tmp_path):
    m = VoiceModeler(make_cfg(tmp_path, "{engine: none, emotion: true, accent: en_AU}"))
    piper = m.engine_params("piper", "cheeky", speed=1.0)
    # moods are played a bit stronger than written by default (emotion_strength: 1.25)
    assert piper["length_scale"] < 1 and piper["noise_scale"] > 0.667 and piper["post"]["pitch"] == 1.875
    calm = m.engine_params("piper", "calm")
    assert calm["length_scale"] > 1 and calm["sentence_silence"] > 0.2
    esp = m.engine_params("espeak", "cheerful")
    assert esp["pitch"] > 50 and esp["post"] == {"pitch": 0.0, "volume": 0.0, "tempo": 1.0}
    loud, quiet = m.engine_params("chatterbox", "annoyed"), m.engine_params("chatterbox", "calm")
    assert loud["exaggeration"] > quiet["exaggeration"] and loud["cfg_weight"] < quiet["cfg_weight"]
    http = m.engine_params("http", "cheeky", speed=1.1)
    assert "Australian" in http["instructions"] and "cheeky" in http["instructions"] and http["base_speed"] == 1.1
    tried = m.engine_params("piper", "cheeky", knobs={"pitch": -3, "nope": 1})
    assert tried["post"]["pitch"] == -3 and m.knobs("cheeky")["pitch"] == EMOTIONS["cheeky"]["pitch"] * 1.25


def test_post_filter_keeps_the_length_when_shifting_pitch():
    chain = tts_neural.post_filter({"pitch": 12, "volume": -3, "tempo": 1.0}, 22050)
    assert "asetrate=44100" in chain and "atempo=0.5000" in chain and "volume=-3.0dB" in chain
    assert tts_neural.post_filter({"pitch": 0, "volume": 0, "tempo": 1.0}, 22050) == ""
    assert tts_neural.post_filter({"tempo": 3.0}, 22050) == "atempo=2.0,atempo=1.5000"


# ---------- the engines, with the programs faked ----------
class Runs:
    def __init__(self):
        self.cmds = []

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        out = next((c for i, c in enumerate(cmd) if i and cmd[i - 1] in ("--output_file", "-w", "-o")), None)
        if out:
            Path(out).write_bytes(b"RIFF" + b"\0" * 100)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")


@pytest.fixture
def runs(monkeypatch):
    r = Runs()
    monkeypatch.setattr(vmod.subprocess, "run", r)
    monkeypatch.setattr(vmod.shutil, "which", lambda name: f"/usr/bin/{name}" if name != "ffmpeg" else None)
    monkeypatch.setattr(tts_neural.shutil, "which", lambda name: None)
    return r


def test_piper_moods_and_the_accent_fallback(tmp_path, runs):
    models = tmp_path / "models" / "piper"
    models.mkdir(parents=True)
    (models / "en_GB-alan-medium.onnx").write_bytes(b"x")
    (models / "en_US-lessac-medium.onnx").write_bytes(b"x")
    cfg = make_cfg(tmp_path, "{engine: piper, piper_model: models/piper/en_US-lessac-medium.onnx}")
    v = Voice(cfg)
    v.synthesize("Hello.", v.base)
    assert runs.cmds[-1][:2] == ["piper", "--model"] and "--noise_scale" not in runs.cmds[-1]  # moods off: as before
    assert runs.cmds[-1][2].endswith("en_US-lessac-medium.onnx")
    cfg2 = make_cfg(tmp_path, "{engine: piper, accent: en_AU, emotion: true, "
                              "piper_model: models/piper/en_US-lessac-medium.onnx}")
    v2 = Voice(cfg2)
    v2.synthesize("Hello.", v2.base, emotion="cheeky")
    cmd = runs.cmds[-1]
    assert cmd[2].endswith("en_GB-alan-medium.onnx")  # no Aussie Piper voice: the closest one
    assert float(cmd[cmd.index("--noise_scale") + 1]) > 0.667  # livelier than Piper's default
    assert float(cmd[cmd.index("--length_scale") + 1]) < 1.0
    n = len(runs.cmds)
    v2.synthesize("Hello.", v2.base, emotion="cheeky")
    assert len(runs.cmds) == n  # cached
    v2.synthesize("Hello.", v2.base, emotion="calm")
    assert len(runs.cmds) == n + 1  # another mood is another sound


def test_missing_piper_voice_file_warns_once_and_falls_back(tmp_path, runs, monkeypatch, caplog):
    vmod._WARNED.discard("piper-model")
    monkeypatch.setattr(vmod.sys, "platform", "linux")
    monkeypatch.setattr(vmod.shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("piper", "espeak-ng") else None)
    cfg = make_cfg(tmp_path, "{engine: piper, piper_model: models/piper/en_US-lessac-medium.onnx}")
    v = Voice(cfg)
    with caplog.at_level("WARNING"):
        v.synthesize("Hello.", v.base)
        v.synthesize("Hello again.", v.base)
    assert not any(c[0] == "piper" for c in runs.cmds)  # never runs Piper without its voice file
    assert any(c[0] == "espeak-ng" for c in runs.cmds)  # says it with espeak meanwhile
    assert sum("Piper voice file" in r.message for r in caplog.records) == 1  # and says why once


def test_mac_say_picks_an_australian_voice(tmp_path, runs, monkeypatch):
    monkeypatch.setattr(vmod, "mac_voice_locales", lambda: {"Samantha": "en_US", "Karen": "en_AU",
                                                            "Lee (Premium)": "en_AU", "Lee": "en_AU"})
    v = Voice(make_cfg(tmp_path, "{engine: say, accent: en_AU, emotion: true}"))
    v.synthesize("Lights off. Try not to trip.", v.base, emotion="calm")
    cmd = runs.cmds[-1]
    assert cmd[cmd.index("-v") + 1] == "Lee (Premium)"
    assert "[[slnc" in cmd[-1] and int(cmd[cmd.index("-r") + 1]) < 180


def test_espeak_does_the_mood_itself(tmp_path, runs):
    v = Voice(make_cfg(tmp_path, "{engine: espeak, espeak_voice: en-us+f3, accent: en_AU, emotion: true}"))
    v.synthesize("Righto.", v.base, emotion="cheerful")
    cmd = runs.cmds[-1]
    assert cmd[cmd.index("-v") + 1] == "en-gb+f3" and int(cmd[cmd.index("-p") + 1]) > 50


def test_a_dead_voice_server_falls_back_to_the_local_voice(tmp_path, runs, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("couldn't reach the voice server")

    monkeypatch.setattr(tts_neural, "http", down)
    v = Voice(make_cfg(tmp_path, "{engine: http, fallback_engine: espeak, http: {url: 'http://nowhere:1/'}}"))
    assert v.synthesize("Smoke alarm in the kitchen!", v.base, emotion="clear").exists()
    assert runs.cmds[-1][0] == "espeak-ng"


def test_chatterbox_needs_consent_for_a_voice_sample(tmp_path):
    sample = tmp_path / "me.wav"
    sample.write_bytes(b"RIFF")
    cfg = make_cfg(tmp_path, "{engine: none}")
    assert tts_neural.chatterbox_sample(cfg, {"sample": str(sample)}) is None
    assert tts_neural.chatterbox_sample(cfg, {"sample": str(sample), "sample_consent": True}) == sample
    assert tts_neural.chatterbox_sample(cfg, {"sample": str(tmp_path / "gone.wav"), "sample_consent": True}) is None


def test_neural_engines_say_how_to_install_them(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "melo", None)
    monkeypatch.setitem(sys.modules, "melo.api", None)
    with pytest.raises(RuntimeError, match="MeloTTS"):
        tts_neural.melo("hi", tmp_path / "x.wav", {}, {}, "en_AU")
    monkeypatch.setitem(sys.modules, "chatterbox", None)
    monkeypatch.setitem(sys.modules, "chatterbox.tts", None)
    with pytest.raises(RuntimeError, match="voice serve"):
        tts_neural.chatterbox("hi", tmp_path / "x.wav", {}, {}, None)


def test_melo_uses_the_australian_speaker(tmp_path, monkeypatch):
    import types

    said = {}

    class TTS:
        def __init__(self, language, device):
            self.hps = types.SimpleNamespace(data=types.SimpleNamespace(spk2id={"EN-US": 0, "EN-AU": 3}))

        def tts_to_file(self, text, spk, path, **kw):
            said.update(text=text, spk=spk, **kw)
            Path(path).write_bytes(b"RIFF")

    monkeypatch.setitem(sys.modules, "melo", types.ModuleType("melo"))
    monkeypatch.setitem(sys.modules, "melo.api", types.SimpleNamespace(TTS=TTS))
    monkeypatch.setattr(tts_neural, "_models", {})
    v = Voice(make_cfg(tmp_path, "{engine: melo, accent: en_AU, emotion: true, melo: {device: cpu}}"))
    v.synthesize("G'day.", v.base, emotion="cheeky")
    assert said["spk"] == 3 and said["speed"] > 1.0 and said["noise_scale"] > 0.6


# ---------- speaking: a mood per line ----------
def test_speak_picks_a_mood_per_line_and_strips_tags(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path, "{engine: espeak, emotion: true, effect: robot, style: mean}")
    from phrasehome.dispatcher import Dispatcher

    home = Dispatcher(cfg)
    v = home.voice
    made = []

    def synth(text, settings=None, emotion=None):
        made.append((text, emotion))
        f = tmp_path / "x.wav"
        f.write_bytes(b"RIFF")
        return f

    monkeypatch.setattr(v, "synthesize", synth)
    monkeypatch.setattr(v, "targets", lambda *a, **k: [None])
    monkeypatch.setattr(v.router, "route", lambda room: "local")
    monkeypatch.setattr(v.router, "play", lambda *a, **k: "local")
    assert v.speak("[annoyed] Again? Seriously.") == "Again? Seriously."
    v.speak("smoke_alarm", room="kitchen")
    v.speak("The lights are on.", emotion="calm")
    v.speak("wake_nag", count="second")
    assert [m for _, m in made] == ["annoyed", "clear", "calm", "annoyed"]
    assert made[1][0].startswith("Smoke alarm")


def test_safety_line_skips_the_robot_effect(tmp_path, runs, monkeypatch):
    applied = []
    monkeypatch.setattr(Voice, "_apply_effect", staticmethod(lambda wav, fx: applied.append(fx)))
    v = Voice(make_cfg(tmp_path, "{engine: espeak, emotion: true, effect: robot}"))
    v.synthesize("Smoke!", v.base, emotion="clear")
    v.synthesize("Lights off.", v.base, emotion="cheeky")
    assert applied == ["robot"]


def test_the_ai_answer_keeps_its_mood_across_sentences():
    from phrasehome.assistant import SentenceSpeaker

    said = []
    done = threading.Event()

    class FakeVoice:
        hush_count = 0

        def speak(self, text, room=None):
            said.append(text)
            if len(said) == 2:
                done.set()

    s = SentenceSpeaker(FakeVoice(), "kitchen")
    s.feed("[cheeky] Lights are off. Try not ")
    s.feed("to trip.")
    s.finish()
    done.wait(5)
    assert said == ["[cheeky] Lights are off.", "[cheeky] Try not to trip."]


# ---------- the voice server on the LAN ----------
def test_voice_server_round_trip(tmp_path):
    from phrasehome.voice_server import VoiceServer

    server_cfg = make_cfg(tmp_path / "laptop", json.dumps(
        {"engine": "command", "emotion": True, "command": [sys.executable, "-c", TONE, "{wav}", "{emotion}"]}))
    laptop = Voice(server_cfg)
    srv = VoiceServer(laptop, host="127.0.0.1", port=0, token="s3cret")
    srv.start()
    try:
        url = f"http://127.0.0.1:{srv.port}/v1/audio/speech"
        health = json.load(urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/health", timeout=10))
        assert health["engine"] == "command" and health["emotion"] is True
        pi = Voice(make_cfg(tmp_path / "pi", json.dumps(
            {"engine": "http", "emotion": True, "fallback_engine": "none",
             "http": {"url": url, "token": "s3cret"}})))
        wav = pi.synthesize("G'day. Lights off.", pi.base, emotion="cheeky")
        # what the laptop itself sends for that line and mood (its own mood processing, done once)
        req = urllib.request.Request(url, data=json.dumps({"input": "G'day. Lights off.", "emotion": "cheeky"}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer s3cret"})
        direct = tmp_path / "direct.wav"
        direct.write_bytes(urllib.request.urlopen(req, timeout=10).read())
        with wave.open(str(wav)) as w, wave.open(str(direct)) as d:
            # the Pi plays exactly what the laptop made: the mood isn't applied a second time
            assert w.getnframes() == d.getnframes() > 0
        moods = list((tmp_path / "laptop").rglob("*.mood"))
        assert [m.read_text() for m in moods] == ["cheeky"]  # the laptop acted the mood out
        wrong = Voice(make_cfg(tmp_path / "pi2", json.dumps(
            {"engine": "http", "fallback_engine": "none", "http": {"url": url, "token": "nope"}})))
        with pytest.raises(RuntimeError, match="401"):
            wrong.synthesize("Hi.", wrong.base)
    finally:
        srv.stop()


def test_voice_server_wont_call_itself(tmp_path):
    from phrasehome.voice_server import VoiceServer

    with pytest.raises(ValueError):
        VoiceServer(Voice(make_cfg(tmp_path, "{engine: http, http: {url: 'http://x'}}")))


# ---------- the dashboard's Voice page ----------
def test_voice_page_and_api(tmp_path):
    from phrasehome.dispatcher import Dispatcher
    from phrasehome.web import WebServer

    cfg = make_cfg(tmp_path, json.dumps(
        {"engine": "command", "command": [sys.executable, "-c", TONE, "{wav}", "{emotion}"]}))
    cfg.web = {"port": 0, "host": "127.0.0.1"}
    web = WebServer(Dispatcher(cfg))
    web.start()
    base = f"http://127.0.0.1:{web.port}"

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.headers["Content-Type"], r.read()

    try:
        page = urllib.request.urlopen(base + "/voice", timeout=10).read().decode()
        assert "Moods" in page and "/api/voice/tune" in page
        info = json.load(urllib.request.urlopen(base + "/api/voice/tune", timeout=10))
        assert set(info["emotions"]) == set(EMOTIONS) and info["engine"] == "command"
        post("/api/voice/tune/options", {"enabled": True, "accent": "en_AU"})
        post("/api/voice/tune/save", {"emotion": "cheeky", "knobs": {"pitch": 3}})
        info = json.load(urllib.request.urlopen(base + "/api/voice/tune", timeout=10))
        assert info["enabled"] and info["accent"] == "en_AU" and info["emotions"]["cheeky"]["knobs"]["pitch"] == 3
        ctype, audio = post("/api/voice/tune/preview", {"text": "Lights off.", "emotion": "annoyed",
                                                        "knobs": {"pitch": -2}})
        assert ctype == "audio/wav" and audio[:4] == b"RIFF"
    finally:
        web.stop()



def test_moods_are_far_enough_apart_to_hear():
    from phrasehome.voice_style import EMOTIONS

    def k(m, n, d):
        return EMOTIONS[m].get(n, d)

    assert k("cheerful", "pitch", 0) - k("annoyed", "pitch", 0) >= 5  # semitones between happy and fed up
    assert k("urgent", "rate", 1) - k("calm", "rate", 1) >= 0.25
    assert k("soft", "volume", 0) <= -4


def test_untagged_sentences_get_a_mood_from_their_words():
    from phrasehome.voice_style import guess

    assert guess("You left the back door open again.") == "annoyed"
    assert guess("Nice one, you nailed it.") == "cheerful"
    assert guess("Take your time, no rush.") == "calm"
    assert guess("Hurry, the bus leaves in 5 minutes.") == "urgent"
    assert guess("The kettle is on.") is None


def test_emotion_strength_scales_every_mood(tmp_path):
    soft = VoiceModeler(make_cfg(tmp_path, "{engine: none, emotion_strength: 0.5}")).knobs("annoyed")
    clear = VoiceModeler(make_cfg(tmp_path, "{engine: none}")).knobs("annoyed")
    base = EMOTIONS["annoyed"]
    assert soft["pitch"] == base["pitch"] * 0.5 and clear["pitch"] == base["pitch"] * 1.25
    assert clear["rate"] > base["rate"] > soft["rate"] > 1.0


def test_piper_speed_is_the_moods_speed(tmp_path):
    """Liveliness used to drive Piper's duration noise, which slowed cheerful down and sped calm up."""
    m = VoiceModeler(make_cfg(tmp_path, "{engine: none}"))
    p = {mood: m.engine_params("piper", mood) for mood in ("neutral", "calm", "cheerful", "annoyed", "soft")}
    assert {x["noise_w"] for x in p.values()} == {0.8}
    assert p["cheerful"]["length_scale"] < p["neutral"]["length_scale"] < p["calm"]["length_scale"]
    assert p["annoyed"]["length_scale"] < p["neutral"]["length_scale"] < p["soft"]["length_scale"]
    assert p["cheerful"]["noise_scale"] > p["calm"]["noise_scale"]  # livelier still sounds livelier
