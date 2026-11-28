"""The house mustn't hear itself: not while it talks, not a moment after, not when the computer's busy and
handles the audio late, and not when a wireless speaker plays it late. People speaking right after are heard."""

import json
import types

import numpy as np
import pytest

from phrasehome import audio_out


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(audio_out, "time", types.SimpleNamespace(monotonic=lambda: now["t"]))
    for name, value in (("_busy", 0), ("_quiet_until", 0.0), ("ECHO_TAIL", 0.8)):
        monkeypatch.setattr(audio_out, name, value)
    monkeypatch.setattr(audio_out, "_local_spoken", audio_out.deque(maxlen=40))
    monkeypatch.setattr(audio_out, "_spoken", audio_out.deque(maxlen=80))
    return now


def say(clock, text, seconds):
    audio_out.speaking_text = text
    audio_out._speaking(True)
    start = clock["t"]
    clock["t"] += seconds
    audio_out._speaking(False)
    return start


def test_muted_by_when_it_was_recorded_not_when_it_was_handled(clock):
    start = say(clock, "Lights off. Enjoy sitting in the dark.", 3.0)
    clock["t"] += 5  # the computer was busy: this audio is only being handled now
    assert audio_out.busy_at(start + 1.0)            # recorded while it talked
    assert audio_out.busy_at(start + 3.5)            # recorded in the echo tail
    assert not audio_out.busy_at(start + 4.5)        # recorded after: a person
    assert not audio_out.local_busy()                # (what the old check asked: wrong for late audio)


def test_its_own_words_coming_back(clock):
    start = say(clock, "House meeting booked for tomorrow at seven. Lights on in the living room.", 4.0)
    assert audio_out.own_echo("house meeting booked for tomorrow at seven lights on", start + 4.5)
    assert audio_out.own_echo("lights on in the living room", start + 6.0)  # a speaker that lags
    assert not audio_out.own_echo("lights on in the living room", start + 20)  # long after: a person
    assert not audio_out.own_echo("turn off the kitchen lights", start + 4.5)  # something else
    say(clock, "Which door? The front or the back?", 2.0)
    assert not audio_out.own_echo("the front", clock["t"] + 0.5)  # a short answer is an answer


def test_room_speakers_count_too(clock):
    audio_out.note_said("Timer set for five minutes. Try not to burn it.", 3.0)
    assert audio_out.own_echo("timer set for five minutes", clock["t"] + 3.5)
    assert not audio_out.busy_at(clock["t"] + 1)  # but the hub's own mic isn't muted for it


class Rec:
    """A recognizer that writes down everything until a quiet chunk ends the sentence."""

    def __init__(self):
        self.words, self.resets = [], 0

    def AcceptWaveform(self, pcm):
        if pcm == b"quiet":
            return bool(self.words)
        self.words.append(pcm.decode())
        return False

    def Result(self):
        text, self.words = " ".join(self.words), []
        return json.dumps({"text": text})

    def PartialResult(self):
        return json.dumps({"partial": " ".join(self.words)})

    def Reset(self):
        self.words, self.resets = [], self.resets + 1


def test_what_it_said_never_reaches_the_command_listener(clock, monkeypatch):
    from phrasehome.listener import Engine

    heard = []
    monkeypatch.setattr("phrasehome.listener.handle_result", lambda result, *a, **k: heard.append(result["text"]))
    cfg = types.SimpleNamespace(sources={"local_mic": {}}, settings=types.SimpleNamespace(
        noise_cleanup=False, second_opinion="off", alternatives=0, min_confidence=0.5), commands=[])
    e = Engine.__new__(Engine)
    main, talk = Rec(), Rec()
    e.config, e.buffers, e._stops, e.router, e.whisper = cfg, {}, None, None, None
    e.recognizers, e.cleaners, e.spotters, e._live, e._names = {"local": main}, {}, {}, {}, []
    e._recognizer = lambda source, room=None: main
    e._talk_recognizer = lambda source, room=None: talk
    e._lang = lambda room: types.SimpleNamespace(matcher=types.SimpleNamespace(match=lambda t: None, near=lambda *a, **k: []))
    e._spot = lambda *a: False
    e._partial = lambda *a: None
    plus = types.SimpleNamespace(pre=lambda s, r, pcm: pcm, echo_on=lambda s: False, fix_text=lambda t: t,
                                 name_needed=lambda *a: None, after=lambda a: None)
    e.__dict__["_hearing_plus"] = plus
    e.dispatcher = types.SimpleNamespace(state=types.SimpleNamespace(is_private=lambda r: False), confirm=None,
                                         voice=types.SimpleNamespace(hush=lambda: None))
    e._say_again = lambda *a: None
    e._second_guess = lambda *a: None
    e._count_miss = lambda *a: None
    e._heard = lambda *a: None
    audio_out.speaking_text = "kitchen lights on you're welcome"
    audio_out._speaking(True)
    t0 = clock["t"]
    for i, w in enumerate(["kitchen", "lights", "on", "you're", "welcome"]):
        clock["t"] = t0 + i * 0.25
        e.feed("local", "kitchen", w.encode(), None, at=clock["t"])
    clock["t"] = t0 + 1.25
    audio_out._speaking(False)
    clock["t"] += 1.0
    e.feed("local", "kitchen", b"quiet", None, at=clock["t"])
    assert heard == [] and main.words == []  # nothing of its own voice left to finish off
    for w in (b"lights", b"off", b"quiet"):  # then a person, straight after
        clock["t"] += 0.25
        e.feed("local", "kitchen", w, None, at=clock["t"])
    assert heard == ["lights off"]


# ---------------------------------------------------------------- phrasehome voice echo-test
def _tone(seconds, level=6000, rate=16000):
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * 300 * t) * level).astype(np.int16)


def test_echo_test_measures_delay_and_echo():
    from phrasehome.echo_test import analyse, report

    rate, t0 = 16000, 100.0
    quiet = np.zeros(int(rate * 1.5), dtype=np.int16) + 5
    # the speaker plays 2 s from t0+1.5, but the sound arrives 0.3 s late and echoes for 0.4 s after
    audio = np.concatenate([quiet, np.zeros(int(rate * 0.3), dtype=np.int16), _tone(2.0 + 0.4),
                            np.zeros(int(rate * 2), dtype=np.int16)])
    chunks = [(t0 + (i + 1600) / rate, audio[i:i + 1600].tobytes()) for i in range(0, len(audio), 1600)]
    r = analyse(chunks, [(t0 + 1.5, t0 + 3.5)], rate)
    line = r["lines"][0]
    assert abs(line["latency"] - 0.3) < 0.05 and abs(line["tail"] - 0.7) < 0.05
    out = []
    assert report({**r, "leaked": []}, 0.8, show=out.append) == 1  # 0.7 + 0.3 + 0.3 > 0.8: needs longer
    assert any("echo_tail: 1.3" in x for x in out) and any("wireless" in x for x in out)
    assert report({**r, "leaked": []}, 1.5, show=out.append) == 0


def test_wireless_speakers_get_a_longer_wait():
    from phrasehome.listener import set_echo_tail

    class SD:
        def __init__(self, name):
            self.name = name

        def query_devices(self, kind=None):
            return {"name": self.name}

    assert set_echo_tail({}, SD("MacBook Air Speakers")) == 0.8
    assert set_echo_tail({}, SD("Finn's AirPods Pro")) == 1.5
    assert set_echo_tail({"echo_tail": 2.5}, SD("Living Room (AirPlay)")) == 2.5
    assert audio_out.ECHO_TAIL == 2.5
    audio_out.ECHO_TAIL = 0.8
