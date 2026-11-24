"""Instances: one assistant per room or person, all living on the hub.

Each instance has its own reminders, timers, alarms, lists, memory, wake name, voice, personality
and quiet hours. The main instance (usually the living room) is the household: it can see and
manage every instance, and holds the shared lists (shopping...). Personal instances see their own
data plus the shared lists.

instances:
  household: { main: true, rooms: [living room, kitchen] }
  sam:
    name: Sam
    rooms: [sam's room]
    wake_names: [jarvis]
    voice: { piper_model: models/piper/en_US-amy-medium.onnx, volume: 80 }
    persona: "You are Sam's laid-back room assistant."
    quiet_hours: "22:30-07:00"
    phone: sam-alerts-xyz        # ntfy topic for Sam's reminders
    phone_ip: 192.168.1.50       # optional: presence by pinging the phone
    kid_mode: false
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock

REPEATS = ["none", "daily", "weekdays", "weekends", "weekly"]
DEFAULT_SHARED_LISTS = ["shopping", "groceries", "household"]


@dataclass
class Instance:
    id: str
    name: str
    rooms: list[str]
    main: bool = False
    wake_names: list[str] = field(default_factory=list)
    voice: dict[str, Any] = field(default_factory=dict)
    persona: str = ""
    quiet_hours: str = ""
    phone: str = ""
    phone_ip: str = ""
    kid_mode: bool = False
    calendar: str = ""
    people: list[str] = field(default_factory=list)  # face-bank names that are this person

    def can_manage(self, other: "Instance") -> bool:
        return self.main or other.id == self.id

    def quiet_now(self) -> bool:
        return bool(self.quiet_hours) and clock.in_window(self.quiet_hours, clock.now())

    def matches_person(self, name: str) -> bool:
        n = name.lower()
        return n in (self.id, self.name.lower()) or n in [p.lower() for p in self.people]


def parse_instances(raw: dict[str, Any] | None) -> list[Instance]:
    out = []
    for iid, cfg in (raw or {}).items():
        cfg = dict(cfg or {})
        out.append(Instance(
            id=str(iid).lower(), name=str(cfg.get("name", iid)),
            rooms=[str(r).lower() for r in cfg.get("rooms", [])], main=bool(cfg.get("main")),
            wake_names=[str(w) for w in cfg.get("wake_names", [])], voice=dict(cfg.get("voice") or {}),
            persona=str(cfg.get("persona", "")), quiet_hours=str(cfg.get("quiet_hours", "")),
            phone=str(cfg.get("phone", "")), phone_ip=str(cfg.get("phone_ip", "")),
            kid_mode=bool(cfg.get("kid_mode")), calendar=str(cfg.get("calendar", "")),
            people=[str(p) for p in cfg.get("people", [])],
        ))
    if not any(i.main for i in out):
        main = next((i for i in out if i.id in ("household", "main", "home")), None)
        if main:
            main.main = True
        else:
            out.insert(0, Instance("household", "Household", [], main=True))
    return out


class Instances:
    """Who owns which room, plus each instance's data (data/instances/<id>.json)."""

    def __init__(self, instances: list[Instance], folder: Path, events=None,
                 shared_lists: list[str] | None = None) -> None:
        self.all = {i.id: i for i in instances}
        self.main = next(i for i in instances if i.main)
        self.folder = Path(folder)
        self.events = events
        self.shared_lists = [s.lower() for s in (shared_lists or DEFAULT_SHARED_LISTS)]
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}

    # ---------- lookup ----------
    def for_room(self, room: str | None) -> Instance:
        if room:
            r = room.lower()
            for i in self.all.values():
                if r in i.rooms:
                    return i
        return self.main

    def get(self, ref: str | None, asker: Instance | None = None) -> Instance:
        """Find an instance by id or person name ("" / "me" = the asker). Enforces permissions."""
        asker = asker or self.main
        if not ref or str(ref).lower() in ("me", "my", "mine", "myself", asker.id, asker.name.lower()):
            return asker
        ref = str(ref).lower()
        target = next((i for i in self.all.values() if i.matches_person(ref)), None)
        if target is None:
            raise KeyError(f"no one called '{ref}' here (known: {', '.join(i.name for i in self.all.values())})")
        if not asker.can_manage(target):
            raise PermissionError(f"only the main room can see or change {target.name}'s things")
        return target

    def rooms_of(self, inst: Instance) -> list[str]:
        return inst.rooms

    # ---------- storage ----------
    def _load(self, iid: str) -> dict[str, Any]:
        if iid not in self._data:
            try:
                self._data[iid] = json.loads((self.folder / f"{iid}.json").read_text())
            except (OSError, ValueError):
                self._data[iid] = {}
            d = self._data[iid]
            d.setdefault("agenda", [])
            d.setdefault("lists", {})
            d.setdefault("memory", [])
            d.setdefault("dnd", False)
        return self._data[iid]

    def _save(self, iid: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        p = self.folder / f"{iid}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data[iid], indent=1))
        tmp.replace(p)

    # ---------- agenda: reminders, alarms, timers, scheduled actions ----------
    def add(self, inst: Instance, kind: str, due: datetime, text: str = "", repeat: str = "none",
            room: str | None = None, action: dict | None = None, label: str = "") -> dict:
        if kind not in ("reminder", "alarm", "timer", "action"):
            raise ValueError("kind must be reminder, alarm, timer or action")
        repeat = (repeat or "none").lower().replace(" ", "")
        days = [d[:3] for d in repeat.split(",")]
        if repeat not in REPEATS and not all(d in clock.DAY_NAMES for d in days):
            raise ValueError(f"repeat must be one of {', '.join(REPEATS)} or days like mon,wed")
        item = {"id": uuid.uuid4().hex[:6], "kind": kind, "text": text, "label": label,
                "due": due.isoformat(timespec="seconds"), "repeat": repeat,
                "room": room or (inst.rooms[0] if inst.rooms else None), "action": action,
                "created": clock.now().isoformat(timespec="seconds")}
        with self._lock:
            self._load(inst.id)["agenda"].append(item)
            self._save(inst.id)
        if self.events:
            self.events.log("agenda.added", instance=inst.id, kind=kind, text=text or label,
                            due=item["due"])
        return item

    def items(self, inst: Instance, kinds: tuple[str, ...] | None = None) -> list[dict]:
        with self._lock:
            items = list(self._load(inst.id)["agenda"])
        if kinds:
            items = [i for i in items if i["kind"] in kinds]
        return sorted(items, key=lambda i: i["due"])

    def cancel(self, inst: Instance, ref: str = "", kinds: tuple[str, ...] | None = None) -> list[dict]:
        """Cancel by id, by words in the text/label, or (ref empty) the soonest of `kinds`."""
        ref = (ref or "").lower().strip()
        with self._lock:
            agenda = self._load(inst.id)["agenda"]
            pool = [i for i in agenda if not kinds or i["kind"] in kinds]
            if not ref:
                hit = sorted(pool, key=lambda i: i["due"])[:1]
            else:
                hit = [i for i in pool if i["id"] == ref] or [
                    i for i in pool if ref in (i["text"] + " " + i["label"]).lower()]
            for i in hit:
                agenda.remove(i)
            if hit:
                self._save(inst.id)
        return hit

    def due(self, now: datetime) -> list[tuple[Instance, dict]]:
        out = []
        with self._lock:
            for inst in self.all.values():
                for item in self._load(inst.id)["agenda"]:
                    if datetime.fromisoformat(item["due"]) <= now:
                        out.append((inst, item))
        return out

    def complete(self, inst: Instance, item: dict) -> dict | None:
        """Remove a fired item, or move a repeating one to its next time. Returns the new item."""
        with self._lock:
            agenda = self._load(inst.id)["agenda"]
            if item in agenda:
                agenda.remove(item)
            nxt = None
            if item["repeat"] != "none":
                due = datetime.fromisoformat(item["due"])
                weekly = item["repeat"] == "weekly"
                current = clock.now()
                for _ in range(800):  # next matching day that's in the future
                    due += timedelta(days=7 if weekly else 1)
                    if due > current and (weekly or clock.day_matches(item["repeat"], due.date())):
                        break
                nxt = {**item, "due": due.isoformat(timespec="seconds")}
                agenda.append(nxt)
            self._save(inst.id)
            return nxt

    def snooze(self, inst: Instance, item: dict, minutes: float = 9) -> dict:
        due = clock.now() + timedelta(minutes=minutes)
        return self.add(inst, item["kind"], due, item["text"], "none", item["room"], item["action"],
                        item["label"])

    # ---------- lists ----------
    def _list_owner(self, inst: Instance, name: str) -> Instance:
        return self.main if name.lower() in self.shared_lists else inst

    def list_add(self, inst: Instance, name: str, items: list[str]) -> list[str]:
        name = _list_name(name)
        owner = self._list_owner(inst, name)
        with self._lock:
            lst = self._load(owner.id)["lists"].setdefault(name, [])
            for it in items:
                it = it.strip()
                if it and it.lower() not in (x.lower() for x in lst):
                    lst.append(it)
            self._save(owner.id)
            return list(lst)

    def list_remove(self, inst: Instance, name: str, items: list[str]) -> list[str]:
        name = _list_name(name)
        owner = self._list_owner(inst, name)
        with self._lock:
            lst = self._load(owner.id)["lists"].setdefault(name, [])
            drop = {i.strip().lower() for i in items}
            lst[:] = [x for x in lst if x.lower() not in drop]
            self._save(owner.id)
            return list(lst)

    def list_get(self, inst: Instance, name: str) -> list[str]:
        name = _list_name(name)
        with self._lock:
            return list(self._load(self._list_owner(inst, name).id)["lists"].get(name, []))

    def list_clear(self, inst: Instance, name: str) -> None:
        name = _list_name(name)
        owner = self._list_owner(inst, name)
        with self._lock:
            self._load(owner.id)["lists"][name] = []
            self._save(owner.id)

    def list_names(self, inst: Instance) -> list[str]:
        with self._lock:
            own = set(self._load(inst.id)["lists"])
            shared = set(self._load(self.main.id)["lists"]) & set(self.shared_lists)
        return sorted(own | shared)

    # ---------- memory ----------
    def remember(self, inst: Instance, fact: str) -> None:
        with self._lock:
            mem = self._load(inst.id)["memory"]
            mem.append({"text": fact.strip(), "time": clock.now().strftime("%Y-%m-%d")})
            del mem[:-50]  # keep the 50 most recent
            self._save(inst.id)

    def forget(self, inst: Instance, words: str) -> int:
        w = words.lower().strip()
        with self._lock:
            mem = self._load(inst.id)["memory"]
            keep = [m for m in mem if w not in m["text"].lower()]
            n = len(mem) - len(keep)
            mem[:] = keep
            self._save(inst.id)
            return n

    def memories(self, inst: Instance) -> list[dict]:
        with self._lock:
            return list(self._load(inst.id)["memory"])

    # ---------- do not disturb ----------
    def set_dnd(self, inst: Instance, on: bool) -> None:
        with self._lock:
            self._load(inst.id)["dnd"] = bool(on)
            self._save(inst.id)

    def dnd(self, inst: Instance) -> bool:
        with self._lock:
            return bool(self._load(inst.id)["dnd"])


def _list_name(name: str) -> str:
    n = re.sub(r"\s+list$", "", str(name).strip().lower())
    return n or "todo"
