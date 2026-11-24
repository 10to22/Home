"""Freak mode: a sock on the door, run by the house.

"freak mode" in a room:
  - every other housemate gets a phone notification that someone's getting freaky in there and
    to stay clear (kids, if any, get a neutral "this room is off-limits for a bit")
  - the house stops listening in that room (privacy) and stops making announcements there
  - your `on_start` steps run (dim the lamp, music...)
"freak mode off" (heard even with the mic paused), the dashboard button, or the time running
out ends it: everyone gets the all-clear and `on_end` runs.

freak:
  minutes: 90                       # ends by itself after this
  house_topic: false                # also post to the house's shared ntfy topic
  on_start: [ { node: "{here}", channel: lamp, level: 15 } ]
  on_end:   [ { node: "{here}", channel: lamp, level: 60 } ]

Everyone needs `phone:` (their ntfy topic) on their instance to get the messages.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("phrasehome.freak")


class Freak:
    def __init__(self, home) -> None:
        self.home = home
        cfg = home.config.extra.get("freak") or {}
        self.minutes = float(cfg.get("minutes", 90))
        self.house_topic = bool(cfg.get("house_topic", False))
        self.on_start = cfg.get("on_start") or []
        self.on_end = cfg.get("on_end") or []
        self.active: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, room: str | None, minutes: float | None = None) -> str:
        h = self.home
        room = (room or h.default_room() or "the house").lower()
        minutes = float(minutes or self.minutes)
        inst = h.instances.for_room(room)
        with self._lock:
            again = room in self.active
            gen = (self.active.get(room, {}).get("gen", 0)) + 1
            self.active[room] = {"room": room, "until": time.time() + minutes * 60, "gen": gen,
                                 "by": inst.id, "dnd_before": self.active.get(room, {}).get(
                                     "dnd_before", h.instances.dnd(inst))}
        h.voice.speak("freak_on", room=room, urgent=True, minutes=_n(minutes))
        h.state.set_privacy(True, minutes, room)  # mic off in there
        h.instances.set_dnd(inst, True)  # no announcements in there
        h.events.log("freak.on", room=room, minutes=minutes)
        if not again:
            self._tell_everyone(inst, room, "freak_notify", "freak_notify_kid")
            self._steps(self.on_start, room, "freak mode on")
        h.timeline.call_in(minutes * 60, lambda: self._expire(room, gen))
        return f"freak mode on in the {room} for {_n(minutes)} minutes"

    def stop(self, room: str | None = None, why: str = "") -> str:
        h = self.home
        with self._lock:
            if room not in self.active:
                room = next(iter(self.active), None) if len(self.active) == 1 else room
            st = self.active.pop(room, None) if room else None
        if st is None:
            return "freak mode isn't on"
        inst = h.instances.for_room(room)
        h.state.set_privacy(False, None, room)
        h.instances.set_dnd(inst, bool(st["dnd_before"]))
        h.events.log("freak.off", room=room, why=why or "ended")
        h.voice.speak("freak_off", room=room)
        self._tell_everyone(inst, room, "freak_over", "freak_over_kid")
        self._steps(self.on_end, room, "freak mode off")
        return f"freak mode off in the {room}"

    def _expire(self, room: str, gen: int) -> None:
        if self.active.get(room, {}).get("gen") == gen:
            self.stop(room, why="time's up")

    def _tell_everyone(self, owner, room: str, key: str, kid_key: str) -> None:
        h = self.home
        sent = 0
        for other in h.instances.all.values():
            if other.id == owner.id or not other.phone:
                continue
            k = kid_key if other.kid_mode else key
            text, _ = h.voice.resolve(k, clean=other.kid_mode, room=room, name=owner.name)
            h.notify_person(other, text, title="Freak mode" if not other.kid_mode else "Heads up")
            sent += 1
        if self.house_topic:
            ntfy = next((n for n, b in h.config.backends.items()
                         if b["type"] == "ntfy" or b.get("was") == "ntfy"), None)
            if ntfy:
                text, _ = h.voice.resolve(key, room=room, name=owner.name)
                try:
                    h.run_action({"backend": ntfy, "title": "Freak mode", "message": text})
                    sent += 1
                except Exception as e:
                    log.warning("couldn't post freak mode to the house topic: %s", e)
        if not sent:
            log.warning("freak mode: nobody to notify (give each housemate's instance a `phone:` ntfy topic)")

    def _steps(self, steps: list, room: str, why: str) -> None:
        if steps:
            threading.Thread(target=self.home.run_steps, args=(steps, {"here": room}, why), daemon=True).start()

    def status(self) -> list[dict]:
        now = time.time()
        return [{"room": r, "minutes_left": max(0, round((st["until"] - now) / 60))}
                for r, st in self.active.items()]


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"
