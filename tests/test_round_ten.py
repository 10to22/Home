"""Hearing server, say-again, cancel window, beep listener, quiet calls, shout-outs, up first, updates,
learning sync and the health check."""

import json
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from phrasehome import clock
from phrasehome.matcher import Match
from tests.test_assistant import home  # noqa: F401  (fixture: doors, lockdown, room lights)
from tests.test_wake import Clock


# ---------------------------------------------------------------- 1: hearing server
class FakeWhisper:
    name = "fake-whisper"

    def transcribe(self, pcm, prompt="", language="en"):
        return ("lockdown" if pcm else ""), 0.93, [{"word": "lockdown", "conf": 0.93}]


def test_hearing_server_round_trip():
    from phrasehome.hearing_server import RemoteWhisper, serve

    srv = serve(0, token="s3cret", host="127.0.0.1", whisper=FakeWhisper())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        assert not RemoteWhisper(url).available()  # wrong token
        w = RemoteWhisper(url, "s3cret")
        assert w.available() and "fake-whisper" in w.name
        assert w.transcribe(b"\x01\x00" * 800) == ("lockdown", 0.93, [{"word": "lockdown", "conf": 0.93}])
    finally:
        srv.shutdown()
    with pytest.raises(RuntimeError):
        w.transcribe(b"\x01\x00")  # gone: fails fast, then waits a bit before trying again
    with pytest.raises(RuntimeError, match="retrying"):
        w.transcribe(b"\x01\x00")


# ---------------------------------------------------------------- 3: say that again
def test_two_near_misses_send_the_next_sentence_to_whisper(home, monkeypatch):
    from phrasehome.listener import Engine
    from tests.test_engine_start import _fake_vosk

    _fake_vosk(monkeypatch)
    e = Engine(home.config, object(), home)
    L = e._lang("kitchen")
    e._second_whisper = lambda: FakeWhisper()
    assert e._say_again("mic1", "lock tow", b"\x01" * 3200, "kitchen", L) is None  # no misses yet
    e._count_miss("mic1", "lock tow", L)
    e._count_miss("mic1", "lok tow", L)
    got = e._say_again("mic1", "lock toe", b"\x01" * 3200, "kitchen", L)
    assert got["text"] == "lockdown" and got["alternative_of"] == "lock toe"
    assert not e.__dict__["_misses"].get("mic1")  # reset after
    e._count_miss("mic1", "what a lovely day it is today", L)  # chat isn't a miss
    assert not e.__dict__["_misses"].get("mic1")


# ---------------------------------------------------------------- 6: cancel window
def _lock_match(home):
    cmd = next(c for c in home.config.commands if c.action.get("routine") == "lockdown")
    return Match(cmd, "lockdown", {}, 100.0, "lockdown", room="kitchen", confidence=1.0)


def test_risky_command_waits_for_a_no(home, monkeypatch):
    from phrasehome.listener import run_or_confirm

    ck = Clock(monkeypatch)
    ran = []
    home.submit = lambda m: ran.append(m.command.name) or True
    run_or_confirm(_lock_match(home), home, home.config)
    assert ran == [] and "cancel_window" in str(home.spoken)
    assert home.confirm.answer("kitchen", "no")  # in time
    ck.advance(home, 4)
    assert ran == []
    run_or_confirm(_lock_match(home), home, home.config)
    ck.advance(home, 4)
    assert ran == ["lockdown"]  # nobody objected


def test_fire_never_waits(home):
    from phrasehome.config import Command
    from phrasehome.listener import run_or_confirm

    ran = []
    home.submit = lambda m: ran.append(m.command.name) or True
    home.config.routines["fire_mode"] = []
    fire = Command("fire", ["fire"], {"backend": "routine", "routine": "fire_mode"})
    run_or_confirm(Match(fire, "fire", {}, 100.0, "fire", room="kitchen", confidence=1.0), home, home.config)
    assert ran == ["fire"]


# ---------------------------------------------------------------- 7: beeps
def _beeps(freq, on, off, n):
    R = 16000
    out = [np.zeros(R // 2)]
    t = np.arange(int(R * on)) / R
    for _ in range(n):
        out += [np.sin(2 * np.pi * freq * t) * 9000, np.zeros(int(R * off))]
    return np.concatenate(out + [np.zeros(R)]).astype(np.int16).tobytes()


def test_beep_listener_tells_the_right_person(home, monkeypatch):
    from phrasehome.beeps import Beeps

    sent = []
    monkeypatch.setattr(home, "notify_person", lambda inst, msg, title="", **kw: sent.append((inst.id, msg)))
    home.config.extra["beeps"] = {"washer": {"say": "The washing's done"}}
    b = Beeps(home)
    t = b.add("washer", _beeps(2000, 0.25, 0.35, 4))
    assert abs(t["freq"] - 2000) < 30 and t["count"] == 4
    found = []
    pcm = _beeps(2000, 0.25, 0.35, 4)
    for i in range(0, len(pcm), 1600):
        found += b.feed("kitchen", pcm[i:i + 1600])
    assert found == ["washer"]
    assert home.events.query("appliance.beep")[-1]["name"] == "washer"
    other = _beeps(3100, 0.25, 0.35, 4)
    assert not any(b.feed("bedroom", other[i:i + 1600]) for i in range(0, len(other), 1600))


# ---------------------------------------------------------------- 9, 14, 15: student house bits
@pytest.fixture
def house(tmp_path, monkeypatch):
    from tests.test_house_life import home as make

    gen = make.__wrapped__(tmp_path, monkeypatch) if hasattr(make, "__wrapped__") else None
    if gen is None:
        pytest.skip("fixture not reusable")
    h = next(gen)
    yield h
    try:
        next(gen)
    except StopIteration:
        pass


def test_calls_go_quiet(house):
    c = house.life.companion
    finn = house.instances.all["finn"]
    c.report("finn", {"category": "call", "app": "zoom.us"})
    assert house.instances.dnd(finn)
    c.report("finn", {"category": "study", "app": "Word"})
    assert not house.instances.dnd(finn)


def test_shout_outs(house):
    L = house.life
    assert L.quick("shout out to jo for dinner", room="finn's room")
    assert L.points.totals()["jo"] == 1 and "shout_out" in [k for k, _ in house.said]
    assert L.quick("shout out to finn", room="finn's room") and house.said[-1][0] == "shout_self"
    assert L.points.totals()["finn"] == 0


def test_first_one_up_gets_a_point(house, monkeypatch):
    from datetime import datetime

    morning = datetime.now().astimezone().replace(hour=7, minute=10)
    monkeypatch.setattr(clock, "now", lambda: morning)
    house.events.log("wake.up", person="parker", instance="parker")
    house.events.log("bed.out", person="finn")
    assert house.life.points.totals()["parker"] == 1 and house.life.points.totals()["finn"] == 0
    assert house.life.points.early_birds()[0] == ("Parker", 1)


# ---------------------------------------------------------------- 21: updates
@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_update_fast_forwards_and_rolls_back(tmp_path):
    from phrasehome import updater

    def git(cwd, *a):
        return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()

    origin, house, dev = tmp_path / "origin.git", tmp_path / "house", tmp_path / "dev"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    for d in (dev,):
        git(d, "config", "user.email", "t@t"), git(d, "config", "user.name", "t"), git(d, "checkout", "-q", "-b", "main")
    (dev / "a.txt").write_text("1")
    git(dev, "add", "."), git(dev, "commit", "-qm", "one"), git(dev, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(house)], check=True)
    assert updater.update(house, checks=[["true"]], show=lambda *a: None)["why"] == "already up to date"
    (dev / "a.txt").write_text("2")
    git(dev, "commit", "-qam", "two"), git(dev, "push", "-q", "origin", "main")
    out = updater.update(house, checks=[["true"]], show=lambda *a: None)
    assert out["updated"] and (house / "a.txt").read_text() == "2"
    before = git(house, "rev-parse", "HEAD")
    (dev / "a.txt").write_text("3")
    git(dev, "commit", "-qam", "three (broken)"), git(dev, "push", "-q", "origin", "main")
    out = updater.update(house, checks=[["false"]], show=lambda *a: None)
    assert out["rolled_back"] and git(house, "rev-parse", "HEAD") == before and (house / "a.txt").read_text() == "2"
    (house / "a.txt").write_text("mine")
    assert "changed files" in updater.update(house, checks=[["true"]], show=lambda *a: None)["why"]


@pytest.mark.skipif(not shutil.which("git"), reason="needs git")
def test_update_goes_back_if_the_selftest_gets_worse(tmp_path, monkeypatch):
    from phrasehome import updater

    def git(cwd, *a):
        return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True).stdout.strip()

    origin, house, dev = tmp_path / "origin.git", tmp_path / "house", tmp_path / "dev"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True)
    git(dev, "config", "user.email", "t@t"), git(dev, "config", "user.name", "t"), git(dev, "checkout", "-q", "-b", "main")
    (dev / "a.txt").write_text("1")
    git(dev, "add", "."), git(dev, "commit", "-qm", "one"), git(dev, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", "-b", "main", str(origin), str(house)], check=True)
    (dev / "a.txt").write_text("2")
    git(dev, "commit", "-qam", "two"), git(dev, "push", "-q", "origin", "main")
    before = git(house, "rev-parse", "HEAD")
    crashes = iter([0, 3])  # the old version: no crashes; the new one: three
    monkeypatch.setattr(updater, "selftest_crashes", lambda repo, cfg: next(crashes))
    out = updater.update(house, config_path="c.yaml", checks=[["true"]], selftest=True, show=lambda *a: None)
    assert out["rolled_back"] and "0 to 3" in out["why"] and git(house, "rev-parse", "HEAD") == before


# ---------------------------------------------------------------- 22: sync
def test_learning_merges_both_ways(home, tmp_path):
    from phrasehome.sync import bundle, merge_file, merge_in
    from phrasehome.understanding import Aliases

    Aliases(home.config).add("lock tow", "lockdown")
    theirs = {"aliases.json": [{"heard": "kitchin lights on", "command": "room_lights_on", "slots": {"room": "kitchen"},
                                "added": "2026-01-01"}],
              "accent.json": {"kitten>kitchen": 5}, "accent_trained.json": {"lice": "lights"}}
    merge_in(home.config, theirs, home)
    b = bundle(home.config)
    assert {a["heard"] for a in b["aliases.json"]} == {"lock tow", "kitchin lights on"}
    assert b["accent.json"]["kitten>kitchen"] == 5 and b["accent_trained.json"]["lice"] == "lights"
    assert merge_file("accent.json", {"a>b": 3}, {"a>b": 1, "c>d": 2}) == {"a>b": 3, "c>d": 2}


# ---------------------------------------------------------------- 23: health
def test_health_check_says_what_to_fix(home):
    from phrasehome.doctor import report

    r = report(home, online=False)
    names = {c["name"]: c for c in r["checks"]}
    assert "Speech model (Vosk)" in names and "ffmpeg" in names and "Data folder" in names
    assert names["Data folder"]["ok"]
    bad = [c for c in r["checks"] if c["ok"] is False]
    assert all(c["fix"] for c in bad)  # every problem comes with what to do
