"""Room-level presence: which rooms are occupied, who is in which room, lights that follow you,
bed sensors with a sleep log, and Bluetooth tags ("where are my keys?", "where's the cat?").

rooms:
  occupancy:                       # presence sensors (mmWave LD2410 / PIR) per room
    kitchen: { node: kitchen, input: presence }
    living room: { node: living room, input: presence }
  empty_after: 120                 # seconds with no presence before a room counts as empty
  person_minutes: 20               # how long a sighting of someone (voice, tag, bed) keeps them "in" a room
  nodes: { hallway: upstairs hall } # if a device node's name isn't its room's name
follow_lights:                     # lights that follow you
  kitchen: { light: { node: kitchen, channel: lights }, only_dark: true, off_after: 180 }
  hallway: { light: { node: hallway, channel: lamp }, level: 30, off_after: 60 }
beds:
  sam: { node: bedroom, input: bed }       # pressure mat / load cell on a device node
tags:                              # cheap Bluetooth tags (iTag/NUT/iBeacon) seen by device nodes
  keys:   { mac: "aa:bb:cc:dd:ee:01", owner: sam, name: "Sam's keys" }
  wallet: { mac: "aa:bb:cc:dd:ee:02", owner: sam, carried: true }   # carried: also shows where Sam is
  luna:   { mac: "aa:bb:cc:dd:ee:03", pet: true, name: Luna }

Events: room.occupied / room.empty {room}, person.room {person, room, source},
        bed.in / bed.out {person}, bed.all_in, sleep.logged {person, hours}, tag.moved {tag, room}
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.rooms")


class RoomTracker:
    def __init__(self, home) -> None:
        self.home = home
        cfg = dict(home.config.extra.get("rooms") or {})
        self.cfg = cfg
        self.occupancy_inputs = {str(r).lower(): v for r, v in (cfg.get("occupancy") or {}).items()}
        self.node_rooms = {str(k).lower(): str(v).lower() for k, v in (cfg.get("nodes") or {}).items()}
        self.empty_after = float(cfg.get("empty_after", 120))
        self.person_seconds = float(cfg.get("person_minutes", 20)) * 60
        self.occupied: dict[str, float] = {}           # room -> since
        self.people: dict[str, tuple[str, float, str]] = {}  # person -> (room, time.time(), source)
        self.follow = {str(r).lower(): v for r, v in (home.config.extra.get("follow_lights") or {}).items()}
        self._lit: set[str] = set()
        self.beds = {str(p).lower(): v for p, v in (home.config.extra.get("beds") or {}).items()}
        self.in_bed: dict[str, float] = {}             # person -> since (time.time())
        self._night: dict[str, dict] = {}               # person -> {"start", "outs"}
        self.sleep_path = Path(home.config.resolve_path(home.config.data_dir)) / "sleep.json"
        try:
            self.sleep_log: dict[str, list] = json.loads(self.sleep_path.read_text())
        except (OSError, ValueError):
            self.sleep_log = {}
        self.locator = Locator(home, self)
        home.rooms = self
        home.events.subscribe(self._on_event)

    # ---------- where people are ----------
    def room_of_node(self, node: str) -> str:
        return self.node_rooms.get(node.lower(), node.lower())

    def saw(self, person: str, room: str, source: str) -> None:
        person, room = person.lower(), room.lower()
        prev = self.people.get(person)
        self.people[person] = (room, time.time(), source)
        if not prev or prev[0] != room:
            self.home.events.log("person.room", person=person, room=room, source=source)

    def where(self, person: str) -> tuple[str, float] | None:
        """(room, minutes ago) if seen recently."""
        p = self.people.get(person.lower())
        if not p or time.time() - p[1] > self.person_seconds:
            return None
        return p[0], (time.time() - p[1]) / 60

    def people_in(self, room: str) -> list[str]:
        room = room.lower()
        out = []
        for person, (r, t, _) in self.people.items():
            if r != room or time.time() - t > self.person_seconds:
                continue
            if room in self.occupancy_inputs and room not in self.occupied and time.time() - t > 120:
                continue  # the room's sensor says it's empty now
            out.append(person)
        return sorted(out)

    def is_occupied(self, room: str) -> bool | None:
        room = room.lower()
        if room not in self.occupancy_inputs:
            return None
        return room in self.occupied

    def snapshot(self) -> dict[str, Any]:
        rooms: dict[str, dict] = {}
        for r in set(self.occupancy_inputs) | {v[0] for v in self.people.values()}:
            rooms[r] = {"occupied": self.is_occupied(r), "people": self.people_in(r)}
        return {"rooms": rooms, "in_bed": sorted(self.in_bed),
                "tags": {t: self.locator.where(t) for t in self.locator.tags}}

    # ---------- events ----------
    def _on_event(self, ev: dict) -> None:
        k = ev["kind"]
        if k == "sensor" and "state" in ev:
            self._sensor(ev)
        elif k == "scan" and ev.get("result") == "allowed":
            who = self._person_for_name(ev.get("name", ""))
            room = self.home.config.doors.get(ev.get("door", ""), {}).get("room")
            if who and room:
                self.saw(who, room, "door scan")
        elif k == "command" and ev.get("person") and ev.get("room"):
            self.saw(ev["person"], ev["room"], "voice")

    def _person_for_name(self, name: str) -> str | None:
        n = (name or "").lower()
        for inst in self.home.instances.all.values():
            if not inst.main and inst.matches_person(n):
                return inst.id
        return None

    def _sensor(self, ev: dict) -> None:
        node, inp, st = ev.get("node", ""), ev.get("input", ""), ev.get("state")
        for room, ref in self.occupancy_inputs.items():
            if ref["node"] == node and ref["input"] == inp:
                if st == "on":
                    if room not in self.occupied:
                        self.occupied[room] = time.time()
                        self.home.events.log("room.occupied", room=room)
                    self._follow_on(room)
                elif st == "off":
                    self.home.timeline.call_in(self.empty_after, lambda r=room, n=node, i=inp: self._maybe_empty(r, n, i))
        for person, ref in self.beds.items():
            if ref["node"] == node and ref["input"] == inp and st in ("on", "off"):
                self._bed(person, st == "on")

    def _maybe_empty(self, room: str, node: str, inp: str) -> None:
        cur = self.home.sensors.current(node, inp) if self.home.sensors else None
        if cur and cur.get("state") == "on":
            return
        if self.occupied.pop(room, None) is not None:
            self.home.events.log("room.empty", room=room)
        self._follow_off(room)

    # ---------- lights follow you ----------
    def _follow_on(self, room: str) -> None:
        f = self.follow.get(room)
        if not f or room in self._lit:
            return
        if f.get("only_dark", True):
            from .automation import sun_is_down

            if not sun_is_down(self.home):
                return
        light = dict(f["light"])
        light.setdefault("backend", self.home.config.settings.default_backend)
        if f.get("level"):
            light["level"] = f["level"]
        self._lit.add(room)
        threading.Thread(target=self.home.run_action, args=({**light, "state": True},), daemon=True).start()

    def _follow_off(self, room: str) -> None:
        f = self.follow.get(room)
        if not f or room not in self._lit:
            return
        delay = max(0.0, float(f.get("off_after", 0)) - self.empty_after)

        def off():
            if room in self.occupied:
                return
            self._lit.discard(room)
            light = dict(f["light"])
            light.setdefault("backend", self.home.config.settings.default_backend)
            self.home.run_action({**light, "state": False})
        if delay:
            self.home.timeline.call_in(delay, off)
        else:
            threading.Thread(target=off, daemon=True).start()

    # ---------- beds and sleep ----------
    def _bed(self, person: str, in_bed: bool, now: datetime | None = None) -> None:
        now = now or clock.now()
        if in_bed:
            if person in self.in_bed:
                return
            self.in_bed[person] = now.timestamp()
            room = self.beds[person].get("room") or self.room_of_node(self.beds[person]["node"])
            self.saw(person, room, "bed")
            self.home.events.log("bed.in", person=person)
            night = self._night.get(person)
            if night and night.get("out_at") and now.timestamp() - night["out_at"] < 20 * 60:
                night.pop("out_at")  # back from the bathroom: same night
            elif now.hour >= 18 or now.hour < 4:
                self._night[person] = {"start": now.timestamp()}
            home_people = [p for p in self.home.state.home_people()] or list(self.beds)
            if home_people and all(p in self.in_bed for p in home_people if p in self.beds):
                self.home.events.log("bed.all_in", people=",".join(sorted(self.in_bed)))
        else:
            start = self.in_bed.pop(person, None)
            if start is None:
                return
            self.home.events.log("bed.out", person=person)
            night = self._night.get(person)
            if night:
                night["out_at"] = now.timestamp()
                if 4 <= now.hour < 14 and now.timestamp() - night["start"] > 3 * 3600:
                    self._log_sleep(person, night["start"], now.timestamp())
                    self._night.pop(person, None)

    def _log_sleep(self, person: str, start: float, end: float) -> None:
        hours = round((end - start) / 3600, 2)
        entry = {"date": datetime.fromtimestamp(end).strftime("%Y-%m-%d"),
                 "in": datetime.fromtimestamp(start).strftime("%H:%M"),
                 "out": datetime.fromtimestamp(end).strftime("%H:%M"), "hours": hours}
        self.sleep_log.setdefault(person, []).append(entry)
        self.sleep_log[person] = self.sleep_log[person][-120:]
        self.sleep_path.parent.mkdir(parents=True, exist_ok=True)
        self.sleep_path.write_text(json.dumps(self.sleep_log, indent=1))
        self.home.events.log("sleep.logged", person=person, hours=hours)

    def sleep_report(self, person: str) -> str:
        nights = self.sleep_log.get(person.lower(), [])
        if not nights:
            return "I don't have any sleep logged yet."
        last = nights[-1]
        h, m = divmod(round(last["hours"] * 60), 60)
        text = f"Last night: {h} hours {m} minutes, from {last['in']} to {last['out']}."
        week = nights[-7:]
        if len(week) > 2:
            avg = sum(n["hours"] for n in week) / len(week)
            text += f" Your average over the last {len(week)} nights is {avg:.1f} hours."
        return text

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class Locator:
    """Bluetooth tags: device nodes report what they hear (MAC + signal strength); the strongest
    recent signal decides the room."""

    def __init__(self, home, rooms: RoomTracker) -> None:
        self.home = home
        self.rooms = rooms
        self.tags = {str(n).lower(): dict(v) for n, v in (home.config.extra.get("tags") or {}).items()}
        self.by_mac = {v["mac"].lower(): n for n, v in self.tags.items() if v.get("mac")}
        self.heard: dict[str, dict[str, tuple[int, float]]] = {}  # tag -> node -> (rssi, time)
        self.last: dict[str, tuple[str, float]] = {}               # tag -> (room, time)
        home.locator = self

    def update(self, node: str, devices: list[dict[str, Any]], now: float | None = None) -> None:
        now = time.time() if now is None else now
        for d in devices:
            tag = self.by_mac.get(str(d.get("mac", "")).lower())
            if not tag:
                continue
            self.heard.setdefault(tag, {})[node] = (int(d.get("rssi", -100)), now)
        for tag, nodes in self.heard.items():
            recent = {n: r for n, (r, t) in nodes.items() if now - t < 60}
            if not recent:
                continue
            best = max(recent, key=recent.get)
            room = self.rooms.room_of_node(best)
            prev = self.last.get(tag)
            self.last[tag] = (room, now)
            if not prev or prev[0] != room:
                self.home.events.log("tag.moved", tag=tag, room=room)
            cfg = self.tags[tag]
            if cfg.get("carried") and cfg.get("owner"):
                self.rooms.saw(cfg["owner"], room, f"{tag} tag")

    def where(self, tag: str) -> dict[str, Any] | None:
        hit = self.last.get(tag.lower())
        if not hit:
            return None
        return {"room": hit[0], "minutes_ago": round((time.time() - hit[1]) / 60)}

    def find(self, words: str) -> list[str]:
        w = words.lower()
        return [t for t, cfg in self.tags.items()
                if w in t or w in str(cfg.get("name", "")).lower() or t in w]

    def say_where(self, words: str) -> str:
        hits = self.find(words)
        if not hits:
            return f"I don't have a tag for {words}."
        out = []
        for t in hits:
            name = self.tags[t].get("name", t)
            w = self.where(t)
            if not w:
                out.append(f"I haven't heard {name}'s tag recently.")
            elif w["minutes_ago"] <= 2:
                out.append(f"{name[0].upper() + name[1:]} {'is' if not name.endswith('s') else 'are'} in the {w['room']}.")
            else:
                out.append(f"{name[0].upper() + name[1:]} {'was' if not name.endswith('s') else 'were'} last in the {w['room']}, "
                           f"{w['minutes_ago']} minutes ago.")
        return " ".join(out)
