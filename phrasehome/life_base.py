"""Shared bits for the house life features: config, a little JSON store, speaking, phones, names."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any


class Part:
    section = ""  # its config.yaml section
    store = ""    # data/life/<store>.json

    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get(self.section) or {}) if self.section else {}
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "life" / f"{self.store or self.section}.json"
        self._lock = threading.RLock()

    # ---------- storage ----------
    def load(self, default: Any = None) -> Any:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {} if default is None else default

    def save(self, data: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, default=str))
        tmp.replace(self.path)

    # ---------- people ----------
    def housemates(self) -> list:
        insts = [i for i in self.home.instances.all.values() if not i.main]
        return insts or [self.home.instances.main]

    def find(self, name: Any):
        if name is None:
            return None
        if hasattr(name, "id"):
            return name
        n = str(name).strip().lower()
        if n.endswith("'s"):
            n = n[:-2]
        return next((i for i in self.home.instances.all.values() if i.matches_person(n)), None)

    def name(self, pid: str | None) -> str:
        inst = self.home.instances.all.get(str(pid or ""))
        return inst.name if inst else (str(pid).title() if pid else "Someone")

    def who_rx(self) -> str:
        names = sorted({n for i in self.home.instances.all.values() if not i.main
                        for n in (i.id, i.name.lower(), *[p.lower() for p in i.people])}, key=len, reverse=True)
        return "|".join(re.escape(n) for n in names) or r"(?!x)x"

    def speaker(self, room: str | None, person: str | None = None):
        insts = self.home.instances
        return insts.all.get(person) if person in insts.all else insts.for_room(room)

    def room_of(self, inst) -> str | None:
        return inst.rooms[0] if inst is not None and inst.rooms else None

    def shared_rooms(self) -> list[str] | None:
        return self.home.instances.main.rooms or None

    # ---------- talking ----------
    def say(self, key: str, room: str | None = None, **v) -> str:
        return self.home.voice.speak(key, room=room, **v)

    def text(self, key: str, **v) -> str:
        return self.home.voice.resolve(key, **v)[0]

    def tell(self, people, key: str, title: str = "", **v) -> int:
        text = self.text(key, **v)
        n = 0
        for inst in people:
            if inst is not None and not inst.main:
                self.home.notify_person(inst, text, title=title)
                n += 1
        return n

    def others(self, inst) -> list:
        return [i for i in self.housemates() if inst is None or i.id != inst.id]

    # ---------- hooks (override what you need) ----------
    def on_event(self, ev: dict) -> None:
        pass

    def tick(self, now) -> None:
        pass

    def heard(self, said: str, room: str | None, speaker) -> Any:
        return None


def clean(said: str) -> str:
    return re.sub(r"\s+", " ", said.lower().replace("’", "'")).strip(" .!?,")


def join(items) -> str:
    items = [str(i) for i in items if str(i).strip()]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def money(text: str) -> float | None:
    m = re.search(r"\$?(\d+(?:\.\d{1,2})?)\s*(?:dollars|bucks|quid|pounds|euros)?", text)
    return float(m.group(1)) if m else None
