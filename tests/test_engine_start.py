"""The listener's Engine starts with Vosk and with Whisper (regression: hotwords before Whisper existed)."""

import sys
import types

from tests.test_assistant import home  # noqa: F401  (fixture)


def _fake_vosk(monkeypatch):
    class Rec:
        def __init__(self, *a):
            pass

        def SetWords(self, *a):
            pass

        def SetPartialWords(self, *a):
            pass

        def AcceptWaveform(self, pcm):
            return False

        def PartialResult(self):
            return '{"partial": ""}'

    vosk = types.SimpleNamespace(KaldiRecognizer=Rec, SetLogLevel=lambda n: None, Model=lambda p: object())
    monkeypatch.setitem(sys.modules, "vosk", vosk)


def test_engine_starts_with_vosk(home, monkeypatch):
    from phrasehome.listener import Engine

    _fake_vosk(monkeypatch)
    e = Engine(home.config, object(), home)
    assert e.whisper is None and e.prompt


def test_engine_starts_with_whisper_and_sets_hotwords(home, monkeypatch):
    from phrasehome import hearing
    from phrasehome.listener import Engine

    _fake_vosk(monkeypatch)

    class FakeWhisper:
        def __init__(self, model="", compute_type="int8", beam=1):
            self.name, self.beam, self.hotwords = model or "fake", beam, ""

    monkeypatch.setattr(hearing, "Whisper", FakeWhisper)
    home.config.settings.recognizer = "whisper"
    e = Engine(home.config, object(), home)
    assert isinstance(e.whisper, FakeWhisper) and e.whisper.beam >= 1
    e.reload()  # after a command is added by voice
    assert isinstance(e.whisper.hotwords, str)


def test_unclear_on_or_off_asks_instead_of_guessing(home):
    from phrasehome.listener import polarity_unsure
    from phrasehome.matcher import Matcher
    from phrasehome.understanding import candidates_with_aliases

    from phrasehome.config import Command

    home.config.commands.append(Command("room_lights_off", ["turn off the {room} lights"],
                                        {"backend": "dev", "node": "{room}", "channel": "lights", "state": False}))
    m = Matcher(candidates_with_aliases(home.config)).match("turn on the kitchen lights")
    clear = [{"word": w, "conf": 1.0} for w in "turn on the kitchen lights".split()]
    shaky = [dict(x, conf=0.5) if x["word"] == "on" else x for x in clear]
    assert not polarity_unsure(m, clear, home.config)
    assert polarity_unsure(m, shaky, home.config)
    home.config.settings.polarity_confidence = 0
    assert not polarity_unsure(m, shaky, home.config)
