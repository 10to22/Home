"""Who said it: better scoring, 'too close to call', learn-my-voice, learning as it goes."""

import numpy as np
import pytest

from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.voiceid import VoiceID
from tests.test_freak import CONFIG


def unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def voice(seed, n=128):
    return unit(np.random.default_rng(seed).normal(size=n))


def near(v, amount, seed):
    return unit(v + amount * np.random.default_rng(seed).normal(size=len(v)) / np.sqrt(len(v)))


@pytest.fixture
def vid(tmp_path):
    (tmp_path / "c.yaml").write_text(CONFIG + "voice_id: { threshold: 0.55 }\n")
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.voice.dry_run = True
    v = VoiceID(h)
    yield v
    h.close()


def test_tells_people_apart_and_refuses_to_guess_between_look_alikes(vid):
    finn, alex = voice(1), voice(2)
    vid.enroll("Finn", [near(finn, 0.3, s) for s in range(5)])
    vid.enroll("Alex", [near(alex, 0.3, s) for s in range(10, 15)])
    assert vid.identify(near(finn, 0.3, 99))[0] == "finn"
    assert vid.identify(near(alex, 0.3, 98))[0] == "alex"
    halfway = unit(finn + alex)  # sounds equally like both
    person, _ = vid.identify(halfway)
    assert person is None


def test_learn_my_voice_takes_the_next_sentences(vid):
    jo = voice(3)
    vid.start_learning("Jo", sentences=3)
    for s in range(3):
        assert vid.identify(near(jo, 0.3, s), "kitchen") == ("jo", 1.0)
    assert vid.learning is None and len(vid.prints["jo"]) == 3
    assert vid.home.events.query("voice.learned")[-1]["person"] == "jo"
    assert vid.identify(near(jo, 0.3, 50))[0] == "jo"  # recognised afterwards


def test_keeps_learning_when_sure(vid):
    finn = voice(1)
    vid.enroll("Finn", [near(finn, 0.2, s) for s in range(3)])
    vid.enroll("Alex", [near(voice(2), 0.2, s) for s in range(3)])
    before = len(vid.prints["finn"])
    assert vid.identify(near(finn, 0.2, 77))[0] == "finn"
    assert len(vid.prints["finn"]) == before + 1  # one more sample of Finn
    vid.keep = 4
    for s in range(10):
        vid.identify(near(finn, 0.2, 200 + s))
    assert len(vid.prints["finn"]) == 4  # only the newest are kept


def test_dashboard_voices(vid):
    from phrasehome.web import WebServer

    web = WebServer.__new__(WebServer)
    web.home = vid.home
    out = web.handle_post("/api/voice/learn", {"person": "Finn", "sentences": 2})["voices"]
    assert out["learning"] == {"person": "finn", "left": 2, "total": 2}
    vid.identify(voice(5))
    vid.identify(voice(6))
    assert vid.status() == {"people": {"finn": 2}, "learning": None}
    assert web.handle_post("/api/voice/forget", {"person": "finn"})["voices"]["people"] == {}
