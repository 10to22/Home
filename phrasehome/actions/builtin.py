"""Backends that are always available: doors, routines and the voice bank."""

from __future__ import annotations

import logging
import time
from typing import Any

from .base import Backend

log = logging.getLogger("phrasehome")


class DoorsBackend(Backend):
    """{door: front, state: locked}   door can be a name or "all"."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        return self.config.home.doors.set(action["door"], action.get("state", "locked"),
                                          reason=action.get("reason", phrase or "command"))


class RoutineBackend(Backend):
    """{routine: lockdown}: run each step of a routine in order. Steps can be any action,
    or {wait: seconds}. A failing step is logged and the routine carries on."""

    MAX_DEPTH = 5

    def run(self, action: dict[str, Any], phrase: str, _depth: int = 0) -> str:
        name = action["routine"]
        home = self.config.home
        steps = self.config.routines.get(name)
        if steps is None:
            raise ValueError(f"unknown routine '{name}'")
        if _depth > self.MAX_DEPTH:
            raise RuntimeError(f"routine '{name}' nests too deeply (a routine calling itself?)")
        values = {**action.get("_context", {}),
                  **{k: v for k, v in action.items() if k not in ("routine", "backend", "_context")}}
        failed = 0
        home.events.log("routine", name=name)
        for st in steps:
            if "wait" in st and len(st) == 1:
                time.sleep(float(st["wait"]))
                continue
            try:
                if st.get("backend") == "routine":
                    self.run({**home.render(st, values), "_context": values}, phrase, _depth + 1)
                else:
                    home.run_action(st, values, phrase)
            except Exception as e:
                failed += 1
                log.error("routine %s: step %s failed: %s", name, st, e)
        return f"routine {name}" + (f" ({failed} steps failed)" if failed else "")


class VoiceBackend(Backend):
    """{speak: retinal_scan_complete} (a phrase from the voice bank) or {speak: "any text"}."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        values = {**action.get("_context", {}),
                  **{k: v for k, v in action.items() if k not in ("speak", "backend", "_context")}}
        return self.config.home.voice.speak(str(action["speak"]), **values)
