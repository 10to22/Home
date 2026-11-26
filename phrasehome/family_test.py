"""How well the house hears each person in the family (speech-test, once per voice).

    phrasehome family-test --record --person sam -n 50   Sam says 50 of your commands (data/speech-test/people/sam)
    phrasehome family-test                                score every person's recordings

The report says, for each person, how many commands ran right and which ones the house keeps
getting wrong for that voice, so you know whose phrases to add, teach or tune.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import speechtest


def people(config) -> list[str]:
    base = speechtest.folder(config) / "people"
    return sorted(p.name for p in base.iterdir() if (p / "manifest.json").exists()) if base.exists() else []


def score_everyone(config, hear=None, show=print) -> dict[str, dict]:
    hear = hear if hear is not None else speechtest.recognizers(config)
    report: dict[str, dict] = {}
    for person in people(config):
        show(f"\n===== {person} =====")
        res = speechtest.score(config, hear, show=show, person=person)
        best_name, best = max(res.items(), key=lambda kv: kv[1]["commands_right"]) if res else ("", {})
        missed = sorted({r["command"] for r in best.get("rows", []) if not r["ok"]})
        report[person] = {"best": best_name, "commands_right": best.get("commands_right", 0),
                          "clips": best.get("clips", 0), "word_accuracy": best.get("word_accuracy", 0),
                          "missed_commands": missed}
    if report:
        show("\n===== summary =====")
        for person, r in report.items():
            show(f"{person}: {r['commands_right']}/{r['clips']} commands right ({r['best']}), "
                 f"words {r['word_accuracy']}%" + (f"; trouble with: {', '.join(r['missed_commands'])}"
                                                   if r["missed_commands"] else ""))
        (speechtest.folder(config) / "family-report.json").write_text(json.dumps(report, indent=1))
    else:
        show("No one has recorded yet. Run: phrasehome family-test --record --person <name>")
    return report
