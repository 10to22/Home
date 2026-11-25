"""Door locks: remembers whether each door is locked, so "lock" is skipped when it's already
locked and announcements stay accurate. If a door has a `sensor`, its real state is read from
the hardware instead (so a key or the inside thumb-turn is noticed).

doors:
  front:
    lock:   { backend: esp, node: front door, channel: lock, state: on }
    unlock: { backend: esp, node: front door, channel: lock, state: off }
    sensor: { backend: esp, node: front door, channel: lock, locked_when: on }   # optional
    auto_lock_minutes: 3                       # relock if left unlocked (optional)
    contact: { node: front door, input: contact }   # door-closed sensor: only relock when shut
    room: front door                           # where door announcements are spoken
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .dispatcher import Dispatcher

log = logging.getLogger("phrasehome.doors")

LOCKED, UNLOCKED, UNKNOWN = "locked", "unlocked", "unknown"


class Doors:
    def __init__(self, home: "Dispatcher") -> None:
        self.home = home
        self.doors = home.config.doors
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "doors.json"
        self._lock = threading.Lock()
        self._sensor_warned: set[str] = set()
        try:
            self._state: dict[str, str] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._state = {}

    def names(self) -> list[str]:
        return list(self.doors)

    def _targets(self, door: str) -> list[str]:
        door = str(door).lower()
        if door == "all":
            return self.names()
        if door not in self.doors:
            raise ValueError(f"unknown door '{door}' (known: {', '.join(self.doors) or 'none'})")
        return [door]

    def state(self, door: str) -> str:
        door = door.lower()
        sensor = self.doors.get(door, {}).get("sensor")
        if sensor and not self.home.dry_run:
            try:
                be = self.home.backend(sensor["backend"])
                ch = be.get_state(sensor["node"], sensor["channel"])
                want_on = str(sensor.get("locked_when", "on")).lower() in ("on", "true")
                st = LOCKED if bool(ch.get("on")) == want_on else UNLOCKED
                self._remember(door, st)
                self._sensor_warned.discard(door)
                return st
            except Exception as e:
                if door not in self._sensor_warned:  # once, not on every question
                    self._sensor_warned.add(door)
                    log.warning("can't read the %s door's sensor (%s); using the last known state",
                                door, str(e).split(" (Caused by")[0][:120])
        return self._state.get(door, UNKNOWN)

    def states(self) -> dict[str, str]:
        return {d: self.state(d) for d in self.names()}

    def _remember(self, door: str, st: str) -> None:
        if self.home.dry_run:
            self._state[door] = st
            return
        with self._lock:
            if self._state.get(door) != st:
                self._state[door] = st
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self._state))

    def set(self, door: str, want: str, reason: str = "") -> str:
        want = {"lock": LOCKED, "locked": LOCKED, "unlock": UNLOCKED, "unlocked": UNLOCKED}.get(
            str(want).lower())
        if want is None:
            raise ValueError("door state must be locked or unlocked")
        out = []
        for d in self._targets(door):
            if self.state(d) == want:
                out.append(f"{d} already {want}")  # e.g. a match at an unlocked door changes nothing
                continue
            steps = self.doors[d].get("lock" if want == LOCKED else "unlock")
            if not steps:
                out.append(f"{d}: no {('lock' if want == LOCKED else 'unlock')} action configured")
                continue
            for st in steps:
                self.home.run_action(st, {"door": d})
            self._remember(d, want)
            self.home.events.log(f"door.{want}", door=d, reason=reason)
            out.append(f"{d} {want}")
            minutes = self.doors[d].get("auto_lock_minutes")
            if want == UNLOCKED and minutes:
                self.home.timeline.call_in(float(minutes) * 60, lambda d=d: self._auto_lock(d))
        return "; ".join(out)

    def is_open(self, door: str) -> bool | None:
        """From the door's contact sensor, if it has one (True = standing open)."""
        c = self.doors.get(door, {}).get("contact")
        if not c or not self.home.sensors:
            return None
        cur = self.home.sensors.current(c["node"], c["input"])
        if cur is None:
            return None
        closed_when = str(c.get("closed_when", "on")).lower()
        return cur.get("state") != closed_when

    def _auto_lock(self, door: str) -> None:
        if self.state(door) != UNLOCKED:
            return
        if self.is_open(door):
            self.home.timeline.call_in(60, lambda: self._auto_lock(door))  # wait until it's shut
            return
        log.info("auto-locking the %s door", door)
        self.set(door, LOCKED, reason="auto-lock")
        self.home.voice.speak("door_auto_locked", room=self.doors[door].get("room"), door=door)
