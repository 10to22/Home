"""Commands and schedules made by voice ("house, when I say 'cozy', dim the lights and play
jazz", "house, every weekday at 7 turn on the coffee maker"). Saved in data/custom.json and
loaded at startup alongside config.yaml. They can only be built from your existing commands and
routines, so the AI can't invent new device actions.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from .config import Command, normalize_action

log = logging.getLogger("phrasehome.customs")


class Customs:
    def __init__(self, home) -> None:
        self.home = home
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "custom.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {"commands": [], "schedules": []}
        self._lock = threading.Lock()
        home.customs = self
        for c in self.data["commands"]:
            self._install_command(c)
        for s in self.data["schedules"]:
            self._install_schedule(s)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1))

    # ---------- building steps from existing commands/routines ----------
    def steps(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """items: [{command: name, slots: {room: kitchen}}] or [{routine: name}] or [{say: text}]"""
        c = self.home.config
        out = []
        for it in items:
            if it.get("routine"):
                if it["routine"] not in c.routines:
                    raise ValueError(f"no routine called {it['routine']}")
                out.append({"backend": "routine", "routine": it["routine"]})
            elif it.get("command"):
                cmd = next((x for x in c.commands if x.name == it["command"]), None)
                if cmd is None:
                    raise ValueError(f"no command called {it['command']}")
                slots = {s["name"]: s["value"] for s in it.get("slots") or []} if isinstance(it.get("slots"), list) \
                    else dict(it.get("slots") or {})
                for k, v in list(slots.items()):
                    opts = c.slots.get(k, {})
                    slots[k] = opts.get(str(v).lower(), v)
                from .actions import render as fill

                out.append(fill(cmd.action, {k: str(v) for k, v in slots.items()}))  # {here} stays for later
            elif it.get("say"):
                out.append({"backend": "voice", "speak": str(it["say"])})
            elif it.get("wait"):
                out.append({"wait": float(it["wait"])})
        if not out:
            raise ValueError("no steps")
        return [normalize_action(s, c.settings.default_backend, c.backends, "custom step") for s in out]

    # ---------- commands ----------
    def add_command(self, name: str, phrases: list[str], items: list[dict]) -> dict:
        from .matcher import Matcher, expand, normalize

        name = "custom_" + normalize(name).replace(" ", "_")
        phrases = [normalize(p) for p in phrases if normalize(p)]
        if not phrases:
            raise ValueError("give at least one phrase")
        others = Matcher(expand([c for c in self.home.config.commands if c.name != name], self.home.config.slots))
        for p in phrases:
            hit = others.match(p)
            if hit:
                raise ValueError(f"'{p}' is already a command ({hit.command.name})")
        entry = {"name": name, "phrases": phrases, "steps": self.steps(items)}
        with self._lock:
            self.data["commands"] = [c for c in self.data["commands"] if c["name"] != name] + [entry]
            self._save()
        self._install_command(entry)
        self._refresh()
        return entry

    def _install_command(self, entry: dict) -> None:
        c = self.home.config
        c.routines[entry["name"]] = entry["steps"]
        c.commands[:] = [x for x in c.commands if x.name != entry["name"]]
        c.commands.append(Command(name=entry["name"], phrases=entry["phrases"],
                                  action={"backend": "routine", "routine": entry["name"]}))

    # ---------- schedules ----------
    def add_schedule(self, name: str, at: str, days: Any, items: list[dict]) -> dict:
        from . import clock

        if at not in ("sunrise", "sunset"):
            clock.parse_hhmm(at)
        entry = {"name": "custom: " + name, "at": at, "days": days or "daily", "do": self.steps(items)}
        with self._lock:
            self.data["schedules"] = [s for s in self.data["schedules"] if s["name"] != entry["name"]] + [entry]
            self._save()
        self._install_schedule(entry)
        return entry

    def _install_schedule(self, entry: dict) -> None:
        c = self.home.config
        c.schedules[:] = [s for s in c.schedules if s["name"] != entry["name"]] + [entry]

    # ---------- listing / removing ----------
    def list(self) -> dict[str, list[str]]:
        return {"commands": [f"{c['name'][7:]}: {', '.join(c['phrases'])}" for c in self.data["commands"]],
                "schedules": [f"{s['name'][8:]} at {s['at']} ({s['days']})" for s in self.data["schedules"]]}

    def remove(self, name: str) -> int:
        from .matcher import normalize

        n = normalize(name).replace(" ", "_")
        c = self.home.config
        with self._lock:
            before = len(self.data["commands"]) + len(self.data["schedules"])
            gone_cmds = [x["name"] for x in self.data["commands"] if n in x["name"]]
            self.data["commands"] = [x for x in self.data["commands"] if x["name"] not in gone_cmds]
            self.data["schedules"] = [x for x in self.data["schedules"] if normalize(name) not in x["name"].lower()]
            self._save()
            removed = before - len(self.data["commands"]) - len(self.data["schedules"])
        c.commands[:] = [x for x in c.commands if x.name not in gone_cmds]
        for g in gone_cmds:
            c.routines.pop(g, None)
        keep = {s["name"] for s in self.data["schedules"]}
        c.schedules[:] = [s for s in c.schedules if not s["name"].startswith("custom: ") or s["name"] in keep]
        self._refresh()
        return removed

    def _refresh(self) -> None:
        engine = getattr(self.home, "engine", None)
        if engine is not None:
            engine.reload()  # new phrases need a new speech grammar
        if self.home.assistant is not None:
            self.home.assistant._profiles.clear()  # the AI's list of commands changed
