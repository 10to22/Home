"""Self-help for the house: wordings it could mix up, answers without the AI, help, the background
service and automatic backups."""

import pytest

from phrasehome.config import load_config


def _cfg(tmp_path, commands: str, extra: str = ""):
    (tmp_path / "c.yaml").write_text(f"""
data_dir: data
settings: {{ default_backend: dev }}
backends: {{ dev: {{ type: log }} }}
voice: {{ engine: none }}
{extra}
commands:
{commands}
""")
    return load_config(tmp_path / "c.yaml")


# ---------------------------------------------------------------- wordings the house could mix up
def test_wordings_that_could_be_mixed_up(tmp_path):
    from phrasehome.doctor import wording_problems

    cfg = _cfg(tmp_path, """
  - { name: movie, phrases: [movie time], action: { node: a, channel: tv, state: on } }
  - { name: film, phrases: [movie time], action: { node: a, channel: lamp, state: off } }
  - { name: kitchen, phrases: [kitchen lights], action: { node: k, channel: lights, state: on } }
  - { name: kitchin, phrases: [kitchin lights], action: { node: b, channel: lights, state: on } }
  - { name: lamp_on, phrases: [lamp on], action: { node: a, channel: lamp, state: on } }
  - { name: lamp_off, phrases: [lamp off], action: { node: a, channel: lamp, state: off } }
  - { name: time, phrases: ["what's the time"], action: { speak: time_now } }
  - { name: date, phrases: ["what's the date"], action: { speak: date_now } }
""")
    found = " | ".join(wording_problems(cfg))
    assert "'movie time' is a wording of both movie and film" in found
    assert "kitchin lights" in found and "sound the same" in found
    assert "lamp on" not in found  # on / off: the opposite on purpose
    assert "what's the time" not in found  # a clearly different word


# ---------------------------------------------------------------- answers that don't need the AI
from tests.test_house_life import home  # noqa: E402,F401  (fixture: Finn, Parker, Alex, Jo; house life on)


def test_whos_home_without_the_ai(home):
    from phrasehome.reports import REPORTS

    assert "nothing's tracking it" in REPORTS["home"](home, {})
    home.state.set_presence("finn", "home")
    home.state.set_presence("jo", "home")
    home.state.set_presence("parker", "away")
    out = REPORTS["home"](home, {})
    assert out.startswith("Finn and Jo are home") and "Parker is out" in out and "Not sure about Alex" in out
    for p in ("parker", "alex"):
        home.state.set_presence(p, "home")
    assert REPORTS["home"](home, {}).startswith("Everyone's home")


def test_whats_on_today_without_the_ai(home):
    from datetime import timedelta

    from phrasehome import clock
    from phrasehome.reports import REPORTS

    finn = home.instances.all["finn"]
    now = clock.now()
    assert REPORTS["today"](home, {"_context": {"here": "finn's room"}}) == "Nothing on for you today."
    if now.hour < 22:
        home.instances.add(finn, "reminder", now + timedelta(minutes=30), text="call mum")
    home.life.exams.add(finn, "calc", now + timedelta(days=1))
    out = REPORTS["today"](home, {"_context": {"here": "finn's room"}})
    assert out.startswith("Today for Finn:") and "your calc exam tomorrow" in out
    assert now.hour >= 22 or "call mum at" in out
    assert "calc" not in REPORTS["today"](home, {"_context": {"here": "jo's room"}})  # Jo's day, not Finn's


def test_ask_falls_back_when_the_ai_cant(home):
    home.state.set_presence("finn", "home")
    out = home.run_action({"backend": "assistant", "ask": "Who's home?",
                           "fallback": {"backend": "report", "report": "home"}})  # no AI here at all
    assert "Finn is home" in out
    from tests.test_bug_sweep import BrokenAI

    home.assistant = BrokenAI()
    out = home.run_action({"backend": "assistant", "ask": "Who's home?",
                           "fallback": {"backend": "report", "report": "home"}})
    assert "Finn is home" in out and "assistant_error" not in [k for k, _ in home.said]


def test_fallback_in_config_is_checked_like_any_step(tmp_path):
    from phrasehome.config import ConfigError

    cfg = _cfg(tmp_path, "  - { name: brief, phrases: [brief me], action: { ask: hi, fallback: { report: today } } }")
    assert cfg.commands[0].action["fallback"]["backend"] == "report"
    with pytest.raises(ConfigError, match="fallback"):
        _cfg(tmp_path, "  - { name: b, phrases: [b], action: { ask: hi, fallback: { backend: nope, x: 1 } } }")


# ---------------------------------------------------------------- what can I say?
def test_what_can_you_do(home):
    assert home.life.quick("what can you do", room="finn's room")
    said = home.said[-1][1]
    assert said.startswith("I do ") and "help page" in said
    assert home.life.quick("what can I say about food", room="finn's room")
    assert "grams in a cup of flour" in home.said[-1][1]
    assert home.life.quick("what can i say about knitting", room="finn's room")
    assert "not sure about knitting" in home.said[-1][1]


def test_help_page_lists_every_command_by_topic():
    from pathlib import Path

    from phrasehome.help import by_topic, page

    cfg = load_config(Path(__file__).resolve().parents[1] / "config.smarthome.example.yaml")
    topics = by_topic(cfg)
    assert sum(len(v) for v in topics.values()) == len([c for c in cfg.commands if c.phrases])
    names = {k: [n for n, _ in v] for k, v in topics.items()}
    assert "lights_on" in names["lights"] and "lock_door" in names["doors"] and "whos_home" in names["info"]
    assert "hush" in names["modes"] and "life_party_start" in names["house"]
    html = page(cfg)
    import re

    assert "What can I say?" in html and '"lights on"' in html and not re.search(r"\{[a-z]+\}", html)


# ---------------------------------------------------------------- backups
def _house(tmp_path):
    (tmp_path / "config.yaml").write_text("data_dir: data\nsettings: { default_backend: dev }\n"
                                          "backends: { dev: { type: log } }\nvoice: { engine: none }\n"
                                          "commands: [ { name: a, phrases: [a], action: { speak: hi } } ]\n")
    d = tmp_path / "data"
    (d / "life").mkdir(parents=True)
    (d / "life" / "points.json").write_text('{"finn": 5}')
    (d / "voice_cache").mkdir()
    (d / "voice_cache" / "x.wav").write_bytes(b"0" * 1000)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=secret\n")
    return load_config(tmp_path / "config.yaml")


def test_backup_and_restore_never_lose_anything(tmp_path):
    import tarfile

    from phrasehome import backup

    cfg = _house(tmp_path)
    f = backup.make(cfg, tmp_path / "config.yaml")
    assert f.parent == tmp_path / "backups" and oct(f.stat().st_mode)[-3:] == "600"
    names = tarfile.open(f).getnames()
    assert "data/life/points.json" in names and ".env" in names and "config.yaml" in names
    assert not any("voice_cache" in n for n in names)  # caches aren't worth keeping
    (tmp_path / "data" / "life" / "points.json").write_text('{"finn": 0}')  # oh no
    out = backup.restore(cfg, f)
    assert (tmp_path / "data" / "life" / "points.json").read_text() == '{"finn": 5}'
    assert '{"finn": 0}' in (tmp_path / out["old_data"].split("/")[-1] / "life" / "points.json").read_text()
    assert (tmp_path / "config.restored.yaml").exists()  # yours isn't overwritten


def test_old_backups_are_pruned(tmp_path):
    from phrasehome import backup

    cfg = _house(tmp_path)
    d = tmp_path / "backups"
    d.mkdir()
    for day in range(1, 6):
        (d / f"phrasehome-backup-2026110{day}-0330.tar.gz").write_bytes(b"x")
    gone = backup.prune(cfg, keep=3)
    assert [p.name[18:26] for p in gone] == ["20261101", "20261102"]
    assert len(backup.existing(cfg)) == 3


def test_restore_refuses_a_strange_file(tmp_path):
    import io
    import tarfile

    from phrasehome import backup

    cfg = _house(tmp_path)
    bad = tmp_path / "evil.tar.gz"
    with tarfile.open(bad, "w:gz") as t:
        info = tarfile.TarInfo("../../evil.sh")
        info.size = 2
        t.addfile(info, io.BytesIO(b"hi"))
    with pytest.raises(ValueError):
        backup.restore(cfg, bad)
    assert (tmp_path / "data" / "life" / "points.json").exists()  # untouched


# ---------------------------------------------------------------- the background service
def test_mac_service_file(tmp_path):
    import plistlib

    from phrasehome import service

    argv = service.command(str(tmp_path / "config.yaml"), simulate=True, mic=True)
    assert argv[-3:] == ["simulate", "--mic"][-3:] or argv[-2:] == ["simulate", "--mic"]
    p = plistlib.loads(service.plist(argv, str(tmp_path)).encode())
    assert p["Label"] == "com.phrasehome.house" and p["KeepAlive"] and p["RunAtLoad"]
    assert p["ProgramArguments"] == argv
    assert p["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:")  # ffmpeg from Homebrew
    assert service.command("c.yaml")[-1] == "run"
