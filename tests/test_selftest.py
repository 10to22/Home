"""phrasehome selftest: the bug test of a virtual copy of the house."""

import json

from phrasehome.selftest import SelfTest, last_result

CONFIG = """
data_dir: data
settings: { default_backend: esp }
backends:
  esp: { type: esp32, nodes: { kitchen: k.local } }
  phone: { type: ntfy, topic: x }
voice: { engine: none }
instances:
  household: { name: Household, main: true, rooms: [kitchen] }
  finn: { name: Finn, rooms: [finn's room], phone: finn-topic }
  jo: { name: Jo, rooms: [jo's room], phone: jo-topic }
routines:
  movie: [ { node: kitchen, channel: lamp, level: 20 }, { wait: 30 }, { speak: "Enjoy the film." } ]
commands:
  - { name: lamp_on, phrases: [lamp on], action: { node: kitchen, channel: lamp, state: on } }
  - { name: movie_time, phrases: [movie time], action: { routine: movie } }
  - { name: film_time, phrases: [movie time], action: { node: kitchen, channel: lamp, state: off } }
  - { name: my_light, phrases: [my light on], action: { node: "{here}", channel: light, state: on } }
"""


def run(tmp_path, **kw):
    (tmp_path / "c.yaml").write_text(CONFIG)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "points.json").write_text('{"finn": 3}')
    return SelfTest(str(tmp_path / "c.yaml"), show=lambda *a: None, **kw).run()


def test_selftest_finds_the_config_problems(tmp_path):
    r = run(tmp_path)
    kinds = {(p["kind"], p["what"].split("'")[1] if "'" in p["what"] else p["what"]) for p in r["problems"]}
    assert ("wrong command", "movie time") in kinds  # two commands share a wording
    assert any(k == "config" and "finn" in w for k, w in kinds) or \
        any(p["kind"] == "config" for p in r["problems"])  # "my light on" in Finn's room: no module there
    assert r["checked"]["routines"] == 1 and r["checked"]["pages"] > 5 and r["checked"]["sentences"] > 50
    assert r["seconds"] < 60  # the routine's 30 s wait was skipped
    assert not r["ok"] and r["crashes"] == 0


def test_selftest_never_touches_the_real_house(tmp_path):
    run(tmp_path, quick=True)
    data = tmp_path / "data"
    assert json.loads((data / "points.json").read_text()) == {"finn": 3}
    assert sorted(p.name for p in data.iterdir()) == ["points.json", "selftest.json"]  # only its result
    from phrasehome.config import load_config

    assert last_result(load_config(tmp_path / "c.yaml"))["quick"] is True
