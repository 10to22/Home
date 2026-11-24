"""Morning alarms that make sure you're actually up.

When an alarm with `make_sure_up` goes off, the house watches that person's bed sensor and the
motion/presence sensors in the other rooms:

- "stop" while you're still in bed only quiets it: it rings again after `recheck_minutes`
  unless you've got up in the meantime
- you can snooze `snooze_limit` times; after that, snoozing is refused
- you're "up" when you've been out of bed for `up_seconds` (with a bed sensor), or when there's
  movement in another room (without one). Then it stops for good, says how long you took, and
  runs `on_up` (e.g. the good morning routine)
- back in bed within `back_to_bed_minutes` of getting up: it starts again
- from the second nag on, `escalate` runs (e.g. the bedroom lights on full)
- after `give_up_minutes` it stops and texts your phone

Every alarm can change these on the Alarms page of the dashboard (/alarms). Defaults:

wake:
  make_sure_up: true
  recheck_minutes: 3
  snooze_limit: 2
  snooze_minutes: 9
  up_seconds: 45
  back_to_bed_minutes: 10
  give_up_minutes: 45
  escalate: [ { node: "{here}", channel: lights, state: on } ]
  on_up: [ { routine: good_morning } ]

Events: wake.started, wake.quieted, wake.nag {count}, wake.snooze_refused, wake.up {person,
minutes, nags, snoozes}, wake.back_to_bed, wake.gave_up.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("phrasehome.wake")

DEFAULTS: dict[str, Any] = {
    "make_sure_up": True,
    "recheck_minutes": 3,
    "snooze_limit": 2,
    "snooze_minutes": 9,
    "up_seconds": 45,
    "back_to_bed_minutes": 10,
    "give_up_minutes": 45,
    "escalate": [],
    "on_up": [],
}
EDITABLE = ("make_sure_up", "recheck_minutes", "snooze_limit", "snooze_minutes", "up_seconds",
            "back_to_bed_minutes", "give_up_minutes")


class Session:
    """One alarm, from when it first rings until the person is up (or the house gives up)."""

    def __init__(self, wake: "Wake", inst, item: dict, room: str | None, person: str | None,
                 has_bed: bool) -> None:
        self.wake, self.home = wake, wake.home
        self.inst, self.item, self.room = inst, item, room
        self.person, self.has_bed = person, has_bed
        w = item.get("wake") or {}
        self.cfg = {**wake.defaults, **{k: v for k, v in w.items() if k in EDITABLE}}
        self.first_rang = float(w.get("first_rang") or time.time())  # carries across snoozes
        self.snoozes = int(w.get("snoozes", 0))
        self.nags = int(w.get("nags", 0))
        self.state = "ringing"  # ringing | quiet | up | done
        self.out_since: float | None = None
        self.up_at: float | None = None
        self._gen = 0  # invalidates old timers when the state moves on

    # ---------- what the person does ----------
    def in_bed(self) -> bool | None:
        rooms = getattr(self.home, "rooms", None)
        if not (self.has_bed and rooms):
            return None
        return self.person in rooms.in_bed

    def asked_to_stop(self) -> bool:
        """'stop'. Returns True if the alarm may stop for real (they're up)."""
        if self.state in ("up", "done"):
            return True
        if self.in_bed() is False and self.out_since is not None:
            self.got_up("out of bed")
            return True
        self._stop_ringing()
        self.state = "quiet"
        minutes = float(self.cfg["recheck_minutes"])
        self.home.events.log("wake.quieted", person=self.person, room=self.room)
        self.say("wake_still_in_bed", minutes=_num(minutes))
        self._later(minutes * 60, self._recheck)
        return False

    def asked_to_snooze(self) -> bool:
        """'snooze'. Returns True if snoozing is allowed (the caller snoozes the alarm)."""
        if self.snoozes >= int(self.cfg["snooze_limit"]):
            self.home.events.log("wake.snooze_refused", person=self.person, snoozes=self.snoozes)
            self.say("wake_snooze_denied", snoozes=self.snoozes)
            return False
        self.snoozes += 1
        self.state = "done"  # the snoozed alarm starts a new session, which carries the counts on
        self._gen += 1
        self.wake.sessions.pop(self.room, None)
        return True

    def bed_changed(self, in_bed: bool) -> None:
        if self.state == "done":
            return
        if not in_bed:
            self.out_since = time.time()
            gen = self._gen
            self.home.timeline.call_in(float(self.cfg["up_seconds"]),
                                       lambda: gen == self._gen and self._still_out())
            return
        self.out_since = None
        self._gen += 1  # cancel the "still out of bed?" check
        if self.state == "up" and self.up_at and \
                time.time() - self.up_at < float(self.cfg["back_to_bed_minutes"]) * 60:
            self.home.events.log("wake.back_to_bed", person=self.person)
            self.state = "ringing"
            self.say("wake_back_to_bed")
            self._ring()
            self._later(float(self.cfg["give_up_minutes"]) * 60 - (time.time() - self.first_rang),
                        self._give_up)

    def moved_elsewhere(self, room: str) -> None:
        """Movement in another room. Without a bed sensor that means they're up."""
        if self.state in ("ringing", "quiet") and not self.has_bed:
            self.got_up(f"moving in the {room}")

    # ---------- the house's side ----------
    def start(self) -> None:
        self.home.events.log("wake.started", person=self.person, room=self.room,
                             snoozes=self.snoozes, sensor="bed" if self.has_bed else "motion")
        if self.in_bed() is False:  # already up before the alarm: nothing to make sure of
            self.state = "up"
            self.up_at = time.time()
            return
        left = float(self.cfg["give_up_minutes"]) * 60 - (time.time() - self.first_rang)
        self._later(max(left, 60), self._give_up)

    def got_up(self, how: str) -> None:
        if self.state in ("up", "done"):
            return
        self._stop_ringing()
        self.state = "up"
        self.up_at = time.time()
        self._gen += 1
        minutes = max(0, round((self.up_at - self.first_rang) / 60))
        log.info("%s is up (%s) after %d min", self.person or self.inst.name, how, minutes)
        self.home.events.log("wake.up", person=self.person, instance=self.inst.id, name=self.inst.name,
                             minutes=minutes,
                             nags=self.nags, snoozes=self.snoozes, how=how)
        self.say("wake_up", minutes=minutes, nags=self.nags, snoozes=self.snoozes)
        self._steps(self.cfg.get("on_up"), "wake on_up")
        if self.has_bed:  # keep an eye out for sneaking back into bed
            gen = self._gen
            self.home.timeline.call_in(float(self.cfg["back_to_bed_minutes"]) * 60,
                                       lambda: gen == self._gen and self._finish())
        else:
            self._finish()

    def _still_out(self) -> None:
        if self.out_since is not None and self.in_bed() is not True:
            self.got_up("out of bed")

    def _recheck(self) -> None:
        if self.state != "quiet":
            return
        if self.in_bed() is False:
            self.got_up("out of bed")
            return
        self.nags += 1
        self.state = "ringing"
        self.home.events.log("wake.nag", person=self.person, count=self.nags)
        self.say("wake_nag", count=_nth(self.nags))
        if self.nags >= 2:
            self._steps(self.cfg.get("escalate"), "wake escalate")
        self._ring()

    def _give_up(self) -> None:
        if self.state in ("up", "done"):
            return
        self._stop_ringing()
        self.state = "done"
        self.wake.sessions.pop(self.room, None)
        minutes = round((time.time() - self.first_rang) / 60)
        self.home.events.log("wake.gave_up", person=self.person, instance=self.inst.id, name=self.inst.name,
                             minutes=minutes, nags=self.nags)
        self.say("wake_gave_up", minutes=minutes)
        self.home.notify_person(self.inst, f"{self.inst.name} still isn't up ({minutes} min after the alarm).",
                                title="Alarm")

    def _finish(self) -> None:
        self.state = "done"
        if self.wake.sessions.get(self.room) is self:
            self.wake.sessions.pop(self.room, None)

    def _ring(self) -> None:
        from .automation import Ringer

        sched = self.home.scheduler
        old = sched.ringers.pop(self.room, None)
        if old:
            old.stop()
        r = Ringer(self.home, self.inst, self.item, times=4, every=30, say="wake_nag")
        sched.ringers[self.room] = r
        r.ring(first=False)

    def _stop_ringing(self) -> None:
        r = self.home.scheduler.ringers.pop(self.room, None)
        if r:
            r.stop()

    def _later(self, seconds: float, fn) -> None:
        gen = self._gen
        self.home.timeline.call_in(seconds, lambda: gen == self._gen and fn())

    def _steps(self, steps, why: str) -> None:
        if steps:
            values = {"here": self.room, "name": self.inst.name}
            threading.Thread(target=self.home.run_steps, args=(steps, values, why), daemon=True).start()

    def say(self, key: str, **values) -> None:
        self.home.voice.speak(key, room=self.room, urgent=True, name=self.inst.name, **values)

    def status(self) -> dict:
        return {"instance": self.inst.id, "name": self.inst.name, "room": self.room,
                "person": self.person, "state": self.state, "in_bed": self.in_bed(),
                "sensor": "bed" if self.has_bed else "motion", "nags": self.nags,
                "snoozes": self.snoozes, "snooze_limit": int(self.cfg["snooze_limit"]),
                "minutes": round((time.time() - self.first_rang) / 60)}


class Wake:
    def __init__(self, home) -> None:
        self.home = home
        self.defaults = {**DEFAULTS, **(home.config.extra.get("wake") or {})}
        self.sessions: dict[str | None, Session] = {}
        occ = ((home.config.extra.get("rooms") or {}).get("occupancy") or {})
        self.motion = {(str(v["node"]).lower(), str(v["input"])): str(room).lower()
                       for room, v in occ.items() if isinstance(v, dict) and "node" in v}
        home.events.subscribe(self._on_event)
        home.wake = self

    def settings(self, item: dict) -> dict:
        return {**{k: self.defaults[k] for k in EDITABLE},
                **{k: v for k, v in (item.get("wake") or {}).items() if k in EDITABLE}}

    def bed_person(self, inst) -> str | None:
        rooms = getattr(self.home, "rooms", None)
        beds = rooms.beds if rooms else {k.lower(): v for k, v in (self.home.config.extra.get("beds") or {}).items()}
        for p in [inst.extra.get("bed"), inst.id, *inst.people, inst.name]:
            if p and str(p).lower() in beds:
                return str(p).lower()
        return None

    def alarm_fired(self, inst, item: dict) -> None:
        """Called when an alarm starts ringing."""
        if not self.settings(item).get("make_sure_up"):
            return
        person = self.bed_person(inst)
        if person is None and not self.motion:
            log.warning("alarm %s: make_sure_up needs a bed sensor (`beds:`) or motion sensors "
                        "(`rooms: occupancy:`); it will stop when told to", item.get("id"))
            return
        room = item.get("room")
        s = Session(self, inst, item, room, person or inst.id, has_bed=person is not None)
        self.sessions[room] = s
        s.start()

    def session_for(self, room: str | None) -> Session | None:
        if room in self.sessions:
            return self.sessions[room]
        live = [s for s in self.sessions.values() if s.state in ("ringing", "quiet")]
        return live[0] if len(live) == 1 else None  # "stop" said anywhere, only one alarm going

    def snooze_carry(self, session: Session) -> dict:
        """The wake settings for the snoozed alarm, so the counts and the start time carry on."""
        return {**(session.item.get("wake") or {}), "first_rang": session.first_rang,
                "snoozes": session.snoozes, "nags": session.nags}

    def _on_event(self, ev: dict) -> None:
        k = ev.get("kind")
        if k in ("bed.in", "bed.out"):
            for s in list(self.sessions.values()):
                if s.has_bed and s.person == str(ev.get("person", "")).lower():
                    s.bed_changed(k == "bed.in")
        elif k == "sensor" and str(ev.get("state")) == "on":
            room = self.motion.get((str(ev.get("node", "")).lower(), str(ev.get("input", ""))))
            if room:
                for s in list(self.sessions.values()):
                    if room != (s.room or ""):
                        s.moved_elsewhere(room)

    def status(self) -> list[dict]:
        return [s.status() for s in self.sessions.values() if s.state != "done"]

    def recent(self, limit: int = 10) -> list[dict]:
        return [e for e in self.home.events.query(limit=500)
                if e["kind"] in ("wake.up", "wake.gave_up")][-limit:]


def _num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _nth(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")
