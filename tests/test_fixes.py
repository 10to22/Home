"""Fixes from testing on a Mac, face auto-unlock, and the deadpan voice style."""

import shutil
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from phrasehome.config import load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.door_scanner import DoorScanner
from phrasehome.voice import STYLES, Voice, effect_filter
from phrasehome.weather import is_major
from tests.test_door import ALICE, MALLORY, STRANGER, FakeEngine, Recorder, home, scanner, vec, walk_up  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


# ---------- voice: one synthesis per voice, the hub speaker plays once ----------
def test_speak_everywhere_makes_audio_once_and_plays_local_once(home, tmp_path, monkeypatch):
    v = home.voice
    v.dry_run = False
    made, played = [], []
    lock = threading.Lock()

    def synth(text, settings=None, emotion=None):
        made.append(text)
        f = tmp_path / "x.wav"
        f.write_bytes(b"RIFF")
        return f

    monkeypatch.setattr(v, "synthesize", synth)
    monkeypatch.setattr(v, "targets", lambda *a, **k: ["kitchen", "bedroom", "office", None])
    monkeypatch.setattr(v.router, "route", lambda room: "local")

    def play(audio, room, volume=1.0, route=None):
        with lock:
            played.append(room)
        return "local"

    monkeypatch.setattr(v.router, "play", play)
    Voice.speak(v, "All clear.", everywhere=True)
    assert made == ["All clear."] and len(played) == 1


def test_synthesize_temp_files_dont_collide(home, monkeypatch):
    v = home.voice

    def fake_run(cmd, **kw):
        out = Path(cmd[cmd.index("-w") + 1])
        out.write_bytes(b"RIFF")

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("phrasehome.voice.subprocess.run", fake_run)
    errors = []

    def go():
        try:
            v.synthesize("Hello there", {"engine": "espeak", "speed": 1.0})
        except Exception as e:  # pragma: no cover - this is the bug
            errors.append(e)

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert len(list(v.cache.glob("*.wav"))) == 1 and not list(v.cache.glob("*.tmp.wav"))


# ---------- {here} from the dashboard / a schedule ----------
def test_here_falls_back_to_the_main_room(home):
    home.config.sources["local_mic"] = {"room": "Living Room"}
    out = home.render({"backend": "log", "room": "{here}"})
    assert out["room"] == "living room"


# ---------- weather: a watch isn't a warning ----------
def test_watch_is_not_major():
    assert not is_major({"event": "High Wind Watch", "severity": "Severe", "certainty": "Possible"})
    assert not is_major({"event": "Wind Advisory", "severity": "Moderate"})
    assert is_major({"event": "Tornado Warning", "severity": "Extreme", "certainty": "Observed"})
    assert is_major({"event": "Severe Thunderstorm Warning", "severity": "Severe", "certainty": "Likely"})


# ---------- Spotify is pretend in the simulator ----------
def test_spotify_simulated(tmp_path):
    from phrasehome.simulator import prepare

    cfg = load_config(ROOT / "config.smarthome.example.yaml")
    cfg.data_dir = str(tmp_path)
    prepare(cfg, base_port=0)
    home = Dispatcher(cfg)
    from phrasehome.spotify import Spotify

    sp = Spotify(home)
    assert sp.play("jazz", rooms=["kitchen"]) == "Playing jazz."
    assert sp.simple("pause") == "Okay."


# ---------- the door scanner waits for a named camera instead of opening it as a file ----------
def test_scanner_waits_for_named_camera(home, monkeypatch):
    opened = []
    monkeypatch.setattr("phrasehome.door_scanner.Camera", lambda spec: opened.append(spec))
    sc = DoorScanner(home, {"door": "front", "camera": "front door"}, engine=FakeEngine())
    sc._running = True
    monkeypatch.setattr("phrasehome.door_scanner.time.sleep", lambda s: setattr(sc, "_running", False))
    sc._loop()
    assert opened == []


# ---------- auto-unlock: my face opens the locked door, no matter what ----------
def auto_scanner(home, who):
    sc = scanner(home)
    sc.cfg["auto_unlock"] = who
    return sc


def test_auto_unlock_opens_locked_door_even_when_away(home):
    sc = auto_scanner(home, ["Alice"])
    home.doors._remember("front", "locked")
    home.state.set_mode("away")
    d = walk_up(sc, ALICE)
    assert d.result == "allowed" and d.auto_unlock
    assert Recorder.calls == [{"node": "front", "channel": "lock", "state": False}]
    assert home.doors.state("front") == "unlocked"
    assert home.spoken == ["Retinal scan complete. Welcome, Alice."]  # no fingerprint prompt
    assert not home.events.query("scan.awaiting_fingerprint")


def test_auto_unlock_even_if_marked_not_allowed(home):
    sc = auto_scanner(home, ["mallory"])
    home.doors._remember("front", "locked")
    d = walk_up(sc, MALLORY)
    assert d.result == "allowed" and d.auto_unlock
    assert home.doors.state("front") == "unlocked"


def test_auto_unlock_at_unlocked_door_changes_nothing(home):
    sc = auto_scanner(home, ["Alice"])
    home.doors._remember("front", "unlocked")
    walk_up(sc, ALICE)
    assert Recorder.calls == [] and home.doors.state("front") == "unlocked"


def test_others_still_need_fingerprint(home):
    sc = auto_scanner(home, ["Mallory"])
    home.doors._remember("front", "locked")
    d = walk_up(sc, ALICE)
    assert d.result == "allowed" and not d.auto_unlock
    assert home.doors.state("front") == "locked"
    assert home.events.query("scan.awaiting_fingerprint")


def test_auto_unlock_true_means_everyone_allowed(home):
    sc = auto_scanner(home, True)
    home.doors._remember("front", "locked")
    assert walk_up(sc, ALICE).auto_unlock
    home.doors._remember("front", "locked")
    assert walk_up(sc, MALLORY, t0=100).result == "denied"


# ---------- deadpan style, effects, persona ----------
def test_deadpan_style_and_user_phrases_win(home):
    home.config.voice = {"style": "deadpan", "phrases": {"lockdown": "Mine."}}
    v = Voice(home.config, dry_run=True)
    assert v.phrases["door_auto_locked"] == STYLES["deadpan"]["door_auto_locked"]
    assert v.phrases["lockdown"] == "Mine."
    assert "smoke_alarm" not in STYLES["deadpan"]  # safety lines stay plain


def test_effect_filter_fills_rate():
    f = effect_filter("synthetic", 22050)
    assert "asetrate=23373" in f and "aresample=22050" in f
    assert effect_filter("volume=2", 16000) == "volume=2"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("effect", ["synthetic", "robot", "deep", "radio"])
def test_effects_run_in_ffmpeg(tmp_path, effect):
    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        t = np.arange(22050) / 22050
        w.writeframes((np.sin(2 * np.pi * 220 * t) * 8000).astype("<i2").tobytes())
    before = wav.read_bytes()
    Voice._apply_effect(wav, effect)
    assert wav.read_bytes() != before and wav.stat().st_size > 1000


def test_persona_presets():
    from phrasehome.assistant import PERSONAS

    assert set(PERSONAS) >= {"butler", "deadpan"} and "safety" in PERSONAS["deadpan"]


def test_capture_more_samples_with_progress(home):
    class Cam:
        def read(self):
            return [(ALICE, 150)]

    sc = scanner(home)
    sc._camera = Cam()
    import phrasehome.door_scanner as ds

    real = ds.time.sleep
    ds.time.sleep = lambda s: None
    try:
        seen = []
        embs, _ = sc.capture_embeddings(30, progress=lambda n, t: seen.append(n))
    finally:
        ds.time.sleep = real
    assert len(embs) == 30 and seen[-1] == 30
    p = sc.bank.enroll("Alice", embs)
    assert len(p.embeddings) == 31  # the first sample plus 30 more, all kept


def test_auto_unlock_saved_in_face_bank(home):
    sc = scanner(home)
    sc.bank.set_auto_unlock("alice")
    from phrasehome.faces import FaceBank

    again = FaceBank(sc.bank.folder)  # survives a restart
    assert again.find("alice").auto_unlock
    home.doors._remember("front", "locked")
    d = walk_up(sc, ALICE)
    assert d.auto_unlock and home.doors.state("front") == "unlocked"
    sc.bank.set_auto_unlock("alice", False)
    home.doors._remember("front", "locked")
    assert not walk_up(sc, ALICE, t0=100).auto_unlock
    assert home.doors.state("front") == "locked"


# ---------- "retinal scan failed" for an enrolled face ----------
def _blend(a, b, cos):
    """A unit vector with cosine ~`cos` to a (b is mixed in for the rest)."""
    b = b - (b @ a) * a
    b = b / np.linalg.norm(b)
    v = cos * a + np.sqrt(1 - cos * cos) * b
    return v / np.linalg.norm(v)


def test_enrolling_while_the_house_runs_is_seen_and_not_overwritten(home):
    from phrasehome.faces import FaceBank

    sc = scanner(home)  # the running house's bank (Alice, Mallory)
    finn = vec(7)
    cli = FaceBank(sc.bank.folder)  # `phrasehome faces enroll` in another Terminal tab
    cli.enroll("Finn", [finn])
    cli.set_auto_unlock("finn")
    home.doors._remember("front", "locked")
    d = walk_up(sc, finn)  # the running scanner sees Finn straight away
    assert (d.name, d.auto_unlock) == ("Finn", True)
    walk_up(sc, STRANGER, t0=100)  # the scanner saves the bank (a new visitor)...
    assert FaceBank(sc.bank.folder).find("finn").auto_unlock  # ...without wiping Finn


def test_a_visitor_saved_from_my_face_cant_beat_my_enrollment(home):
    sc = scanner(home)
    me = vec(8)
    sc.bank.add_visitor(me)  # the door saved me as a stranger before I enrolled
    sc.bank.enroll("Finn", [_blend(me, vec(9), 0.6)])  # enrolled samples are a bit different
    d = walk_up(sc, me)
    assert d.name == "Finn" and d.result == "allowed"


def test_near_miss_keeps_looking_instead_of_locking(home):
    sc = scanner(home)
    home.doors._remember("front", "unlocked")
    nearly = _blend(ALICE, vec(10), 0.33)  # under 0.40, inside the "not sure" zone
    assert walk_up(sc, nearly, frames=6) is None
    assert home.doors.state("front") == "unlocked" and home.spoken == []
    assert not any(p.kind == "visitor" for p in sc.bank.people.values())


def test_enroll_removes_visitor_copies_of_me(home):
    sc = scanner(home)
    me = vec(11)
    sc.bank.add_visitor(me)
    sc.bank.add_visitor(STRANGER)
    p = sc.bank.enroll("Finn", [me])
    like = sc.bank.visitors_like(p, 0.40)
    assert [v.embeddings[0][:3] for v in like] == [list(map(float, me[:3]))]
