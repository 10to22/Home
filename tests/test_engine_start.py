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
