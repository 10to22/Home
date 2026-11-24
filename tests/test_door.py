"""Door scanner, doors, routines and voice bank — with a fake face recognizer."""

import numpy as np
import pytest

from phrasehome import actions
from phrasehome.actions.base import Backend
from phrasehome.config import ConfigError, load_config
from phrasehome.dispatcher import Dispatcher
from phrasehome.door_scanner import DoorScanner
from phrasehome.faces import Face


class Recorder(Backend):
    calls = []

    def run(self, action, phrase):
        Recorder.calls.append({k: v for k, v in action.items() if k != "backend"})
        return "ok"


actions._TYPES["record"] = Recorder


def vec(seed):
    v = np.random.default_rng(seed).normal(size=128).astype(np.float32)
    return v / np.linalg.norm(v)


ALICE, MALLORY, STRANGER = vec(1), vec(2), vec(3)


class FakeEngine:
    """A "frame" is a list of (embedding, face size)."""

    def faces(self, frame):
        return [Face((0, 0, int(s), int(s)), s, 0.99, raw=e) for e, s in frame]

    def embed(self, frame, face):
        noisy = face.raw + np.random.default_rng(0).normal(scale=0.02, size=128).astype(np.float32)
        return noisy / np.linalg.norm(noisy)

    def crop_jpeg(self, frame, face):
        return b"\xff\xd8fakejpeg"


CONFIG = """
data_dir: data
settings: { default_backend: dev }
sources: { satellites: {} }
backends:
  dev: { type: record }
voice: { engine: none }
doors:
  front:
    lock:   { node: front, channel: lock, state: on }
    unlock: { node: front, channel: lock, state: off }
  back:
    lock:   { node: back, channel: lock, state: on }
door_scanner:
  door: front
  confirm_frames: 3
  repeat_seconds: 30
  min_face_px: 80
routines:
  lockdown:
    - { door: all, state: locked }
    - { node: all, channel: lights, state: off }
    - { node: porch, channel: lights, state: on }
    - { speak: lockdown }
  nested:
    - { routine: lockdown }
    - { speak: "Nested routine finished in the {room}." }
commands:
  - phrases: [lockdown]
    action: { routine: lockdown }
"""


@pytest.fixture
def home(tmp_path):
    (tmp_path / "c.yaml").write_text(CONFIG)
    Recorder.calls = []
    h = Dispatcher(load_config(tmp_path / "c.yaml"))
    h.spoken = []
    real = h.voice.speak
    h.voice.speak = lambda key, **v: h.spoken.append(real(key, **v)) or h.spoken[-1]
    yield h
    h.close()


def scanner(home):
    sc = DoorScanner(home, home.config.door_scanners[0], engine=FakeEngine())
    sc.bank.enroll("Alice", [ALICE])
    sc.bank.enroll("Mallory", [MALLORY], allowed=False)
    return sc


def walk_up(sc, who, frames=3, t0=0.0, size=150):
    out = None
    for i in range(frames):
        out = sc.process([(who, size)], now=t0 + i * 0.3) or out
    return out


def test_allowed_face_scan_complete_and_door_stays_unlocked(home):
    home.doors._remember("front", "unlocked")
    d = walk_up(scanner(home), ALICE)
    assert (d.result, d.name) == ("allowed", "Alice")
    assert home.spoken == ["Retinal scan complete. Welcome, Alice.",
                           "Please place your finger on the scanner."]
    assert home.doors.state("front") == "unlocked"  # a match never locks the door
    assert Recorder.calls == []
    assert home.events.query("scan.awaiting_fingerprint")


def test_not_allowed_face_fails_and_locks(home):
    home.doors._remember("front", "unlocked")
    d = walk_up(scanner(home), MALLORY)
    assert d.result == "denied"
    assert home.spoken == ["Retinal scan failed. Locking the front door."]
    assert Recorder.calls == [{"node": "front", "channel": "lock", "state": True}]
    assert home.doors.state("front") == "locked"


def test_unknown_face_added_to_bank_and_autolocks(home):
    sc = scanner(home)
    home.doors._remember("front", "unlocked")
    d = walk_up(sc, STRANGER)
    assert d.result == "unknown" and d.person_id == "visitor-1"
    assert sc.bank.people["visitor-1"].allowed is False
    assert d.photo and d.photo.exists()
    assert home.doors.state("front") == "locked"
    assert len(Recorder.calls) == 1

    # still standing there: no repeat
    assert walk_up(sc, STRANGER, frames=10, t0=1.0) is None
    # comes back later: recognized as the same visitor, still not allowed; door already locked
    d2 = walk_up(sc, STRANGER, t0=100.0)
    assert (d2.result, d2.person_id) == ("denied", "visitor-1")
    assert len(Recorder.calls) == 1  # no second lock command: it was already locked
    assert sc.bank.people["visitor-1"].seen == 2

    # approve them, and next time it's "scan complete"
    sc.bank.set_allowed("visitor-1", True, name="Grandma")
    d3 = walk_up(sc, STRANGER, t0=200.0)
    assert (d3.result, d3.name) == ("allowed", "Grandma")


def test_needs_confirm_frames_and_ignores_far_faces(home):
    sc = scanner(home)
    assert walk_up(sc, STRANGER, frames=2) is None  # not enough frames yet
    assert walk_up(sc, STRANGER, frames=5, size=40) is None  # too far from the door
    assert home.spoken == []


def test_flicker_between_people_resets(home):
    sc = scanner(home)
    for i, who in enumerate([ALICE, STRANGER, ALICE, STRANGER, ALICE]):
        assert sc.process([(who, 150)], now=i * 0.3) is None


def test_lockdown_routine(home):
    home.doors._remember("front", "unlocked")
    home.doors._remember("back", "locked")
    home.run_action(home.config.commands[0].action)
    assert Recorder.calls == [
        {"node": "front", "channel": "lock", "state": True},   # back was already locked
        {"node": "all", "channel": "lights", "state": False},
        {"node": "porch", "channel": "lights", "state": True},
    ]
    assert home.spoken[-1].startswith("Lockdown engaged")
    assert home.doors.states() == {"front": "locked", "back": "locked"}


def test_nested_routine_passes_values(home):
    home.run_action({"backend": "routine", "routine": "nested", "room": "kitchen"})
    assert home.spoken[-1] == "Nested routine finished in the kitchen."


def test_voice_bank_custom_phrases_and_clips(home, tmp_path):
    v = home.voice
    v.phrases["welcome_home"] = ["Hi {name}!"]
    assert v.resolve("welcome_home", name="Sam") == ("Hi Sam!", None)
    assert v.resolve("anything else at all")[0] == "anything else at all"
    clip = v.bank_dir / "welcome_home.wav"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"RIFF")
    assert v.resolve("welcome_home", name="Sam")[1] == clip


def test_config_errors(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(CONFIG.replace("{ routine: lockdown }\n", "{ routine: nope }\n"))
    with pytest.raises(ConfigError, match="unknown routine"):
        load_config(p)
    p.write_text(CONFIG.replace("door: front\n  confirm", "door: garage\n  confirm"))
    with pytest.raises(ConfigError, match="garage"):
        load_config(p)


def test_smarthome_example_routines_dry_run(tmp_path):
    from pathlib import Path

    cfg = load_config(Path(__file__).resolve().parents[1] / "config.smarthome.example.yaml")
    cfg.data_dir = str(tmp_path)
    cfg.routines["leaving"] = [s for s in cfg.routines["leaving"] if "wait" not in s]
    h = Dispatcher(cfg, dry_run=True)
    try:
        for name in cfg.routines:
            assert "failed" not in h.run_action({"backend": "routine", "routine": name}), name
        sc = cfg.door_scanners[0]
        steps = [st for k in ("on_allowed", "on_denied", "on_unknown", "on_doorbell") for st in sc.get(k, [])]
        for fp in cfg.fingerprint:
            steps += [st for k in ("on_accepted", "on_rejected", "on_lockout") for st in fp.get(k, [])]
        for rule in cfg.automations + cfg.schedules:
            steps += rule["do"]
        failed = h.run_steps(steps, {"door": "front", "name": "visitor 1", "node": "hallway",
                                     "photo": "", "here": "kitchen"})
        assert failed == 0
        for c in cfg.commands:  # every command's action renders and runs (dry)
            if c.action["backend"] != "routine":
                h.run_action(c.action, {"here": "kitchen", **{k: next(iter(v.values()))
                                                              for k, v in cfg.slots.items()}})
    finally:
        h.close()


def test_forget_unknown_faces_option(home):
    cfg = {**home.config.door_scanners[0], "remember_unknown": False}
    sc = DoorScanner(home, cfg, engine=FakeEngine())
    home.doors._remember("front", "unlocked")
    d = walk_up(sc, STRANGER)
    assert d.result == "unknown" and d.photo is None and sc.bank.people == {}
    assert home.doors.state("front") == "locked"
