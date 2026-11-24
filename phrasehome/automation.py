"""Everything time- and event-driven:

- Timeline: run something later ("lock the door in 5 minutes", alarm re-rings, `for:` checks)
- Scheduler: fires reminders, alarms, timers and scheduled actions from every instance's agenda;
  runs `schedules:` (at "07:00", at sunset, on weekdays...); runs vacation light simulation
- Automations: `when` an event happens (sensor, door scan, doorbell, mode, presence...), `if`
  conditions hold (mode, time, sun, doors, who's home), optionally `for` some seconds, `do` steps.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import queue
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from . import clock

log = logging.getLogger("phrasehome.automation")


class Timeline:
    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Callable[[], None]]] = []
        self._ids = itertools.count()
        self._lock = threading.Lock()

    def call_in(self, seconds: float, fn: Callable[[], None]) -> None:
        with self._lock:
            heapq.heappush(self._heap, (time.monotonic() + seconds, next(self._ids), fn))

    def run_due(self) -> None:
        now = time.monotonic()
        while True:
            with self._lock:
                if not self._heap or self._heap[0][0] > now:
                    return
                _, _, fn = heapq.heappop(self._heap)
            try:
                fn()
            except Exception as e:
                log.error("scheduled call failed: %s", e)

    def __len__(self) -> int:
        return len(self._heap)


# ---------------------------------------------------------------- conditions
def sun_is_down(home, at: datetime | None = None) -> bool:
    loc = home.config.location
    at = at or clock.now()
    if "latitude" not in loc:
        return at.hour >= 19 or at.hour < 7  # rough guess without a location
    rise, sett = clock.sun_times(at.date(), float(loc["latitude"]), float(loc["longitude"]), at.tzinfo)
    if rise is None:
        return False
    return at < rise or at >= sett


def check(cond: dict[str, Any] | None, home, values: dict | None = None) -> bool:
    """All listed conditions must hold."""
    if not cond:
        return True
    at = clock.now()
    for key, want in cond.items():
        if key == "mode":
            modes = want if isinstance(want, list) else [want]
            if home.state.mode not in [str(m).lower() for m in modes]:
                return False
        elif key == "not_mode":
            modes = want if isinstance(want, list) else [want]
            if home.state.mode in [str(m).lower() for m in modes]:
                return False
        elif key == "time":
            if not clock.in_window(want, at):
                return False
        elif key == "days":
            if not clock.day_matches(want, at.date()):
                return False
        elif key == "sun":
            if (str(want).lower() == "down") != sun_is_down(home, at):
                return False
        elif key == "door":
            for d, st in want.items():
                if home.doors.state(d) != str(st).lower():
                    return False
        elif key == "nobody_home":
            if bool(want) != (not home.state.home_people()):
                return False
        elif key == "someone_home":
            if bool(want) != bool(home.state.home_people()):
                return False
        elif key in ("home", "away"):
            people = [p.lower() for p in (want if isinstance(want, list) else [want])]
            here = set(home.state.home_people())
            if key == "home" and not all(p in here for p in people):
                return False
            if key == "away" and any(p in here for p in people):
                return False
        elif key == "sensor":
            cur = home.sensors.current(want["node"], want["input"]) if home.sensors else None
            if cur is None or not _sensor_matches(want, cur):
                return False
        elif key == "any":  # any one of a list of condition sets
            if not any(check(c, home, values) for c in want):
                return False
        else:
            raise ValueError(f"unknown condition '{key}'")
    return True


def _sensor_matches(want: dict, cur: dict) -> bool:
    if "state" in want:
        st = want["state"]
        st = "on" if st is True else "off" if st is False else str(st).lower()
        if st in ("on", "off") and cur.get("state") != st:
            return False
    if "below" in want and not (cur.get("value") is not None and cur["value"] < float(want["below"])):
        return False
    if "above" in want and not (cur.get("value") is not None and cur["value"] > float(want["above"])):
        return False
    return True


# ---------------------------------------------------------------- automations
class Automations:
    def __init__(self, home) -> None:
        self.home = home
        self.rules = home.config.automations
        self._last_fired: dict[str, float] = {}
        self._edge: dict[tuple, bool] = {}  # threshold rules fire when crossing, not while below
        self._q: queue.Queue = queue.Queue()
        if self.rules:
            home.events.subscribe(self._q.put)
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            ev = self._q.get()
            if ev is None:
                return
            for rule in self.rules:
                try:
                    w = rule["when"]
                    hit = self.matches(w, ev)
                    if ("below" in w or "above" in w) and "value" in ev:
                        key = (rule["name"], ev.get("node"), ev.get("input"))
                        was, self._edge[key] = self._edge.get(key, False), hit
                        hit = hit and not was
                    if hit:
                        self._triggered(rule, ev)
                except Exception as e:
                    log.error("automation %s: %s", rule["name"], e)

    @staticmethod
    def matches(when: dict, ev: dict) -> bool:
        kind = str(when["event"])
        if ev["kind"] != kind and not ev["kind"].startswith(kind + "."):
            return False
        for k, want in when.items():
            if k == "event":
                continue
            if k in ("below", "above", "state"):
                if not _sensor_matches({k: want}, ev):
                    return False
                continue
            have = ev.get(k)
            wants = want if isinstance(want, list) else [want]
            if have is None or str(have).lower() not in [str(w).lower() for w in wants]:
                return False
        return True

    def _triggered(self, rule: dict, ev: dict) -> None:
        hold = float(rule.get("for", 0) or 0)
        if hold > 0:
            self.home.timeline.call_in(hold, lambda: self._after_hold(rule, ev))
        else:
            self._fire(rule, ev)

    def _after_hold(self, rule: dict, ev: dict) -> None:
        # still true? e.g. "door contact open for 10 minutes"
        w = rule["when"]
        if w["event"] == "sensor" and self.home.sensors:
            cur = self.home.sensors.current(ev.get("node"), ev.get("input"))
            if cur is None or not self.matches(w, {**ev, **cur}):
                return
        elif w["event"].startswith("door.") and ev.get("door"):
            if self.home.doors.state(ev["door"]) != w["event"].split(".", 1)[1]:
                return
        elif w["event"] == "mode" and ev.get("mode") != self.home.state.mode:
            return
        self._fire(rule, ev)

    def _fire(self, rule: dict, ev: dict) -> None:
        name = rule["name"]
        if not check(rule.get("if"), self.home, ev):
            return
        cooldown = float(rule.get("cooldown", 0) or 0)
        if time.monotonic() - self._last_fired.get(name, -1e9) < cooldown:
            return
        self._last_fired[name] = time.monotonic()
        log.info("automation: %s", name)
        values = {k: v for k, v in ev.items() if isinstance(v, (str, int, float)) and k != "kind"}
        self.home.events.log("automation", name=name)
        threading.Thread(target=self.home.run_steps, args=(rule["do"], values, f"automation {name}"),
                         daemon=True).start()

    def close(self) -> None:
        self._q.put(None)


# ---------------------------------------------------------------- scheduler
class Ringer:
    """An alarm or timer that keeps ringing until stopped (or gives up)."""

    def __init__(self, home, inst, item: dict, times: int, every: float) -> None:
        self.home, self.inst, self.item = home, inst, item
        self.left, self.every = times, every
        self.stopped = False
        self.room = item.get("room")

    def ring(self) -> None:
        if self.stopped or self.left <= 0:
            self.home.scheduler.ringers.pop(self.room, None)
            return
        self.left -= 1
        kind = self.item["kind"]
        label = self.item.get("label") or self.item.get("text") or ""
        key = "alarm" if kind == "alarm" else "timer_done"
        self.home.voice.speak("alarm_sound" if kind == "alarm" else "timer_sound", room=self.room,
                              urgent=True)
        self.home.voice.speak(key, room=self.room, urgent=True, label=label, name=self.inst.name,
                              text=self.item.get("text", ""))
        self.home.timeline.call_in(self.every, self.ring)

    def stop(self) -> None:
        self.stopped = True


class Scheduler:
    def __init__(self, home) -> None:
        self.home = home
        self.ringers: dict[str | None, Ringer] = {}
        self._last_minute: str = ""
        self._next_vacation = 0.0
        self._vacation_on: set[int] = set()
        self._pre_alarm_done: set[str] = set()
        self._running = False

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception as e:
                log.error("scheduler: %s", e)
            time.sleep(1)

    def tick(self, now: datetime | None = None) -> None:
        now = now or clock.now()
        self.home.timeline.run_due()
        for inst, item in self.home.instances.due(now):
            self.fire(inst, item)
        self.pre_alarms(now)
        minute = now.strftime("%Y-%m-%d %H:%M")
        if minute != self._last_minute:
            self._last_minute = minute
            self.run_schedules(now)
        if self.home.state.mode == "vacation":
            self.vacation_tick(now)

    # ---------- agenda items ----------
    def fire(self, inst, item: dict) -> None:
        self.home.instances.complete(inst, item)  # removes it (or moves a repeating one on)
        kind, room = item["kind"], item.get("room")
        self.home.events.log(f"agenda.{kind}", instance=inst.id, text=item.get("text") or item.get("label"),
                             room=room)
        if kind == "reminder":
            self.home.voice.speak("reminder_sound", room=room, urgent=True)
            self.home.voice.speak("reminder", room=room, urgent=True, text=item["text"],
                                  name=inst.name)
            self.home.notify_person(inst, f"Reminder: {item['text']}")
        elif kind in ("alarm", "timer"):
            old = self.ringers.pop(room, None)
            if old:
                old.stop()
            r = Ringer(self.home, inst, item, times=10 if kind == "alarm" else 3,
                       every=30 if kind == "alarm" else 15)
            self.ringers[room] = r
            r.ring()
        elif kind == "action":
            values = {"here": room} if room else {}
            try:
                self.home.run_steps([item["action"]], values, "scheduled action")
            except Exception as e:
                log.error("scheduled action failed: %s", e)

    def pre_alarms(self, now: datetime) -> None:
        """Before an alarm: a sunrise fade of the bedroom light, and an `agenda.alarm_soon` event
        (e.g. start the coffee machine). Per instance: sunrise: {minutes: 20, light: {...}},
        pre_alarm_minutes: 10."""
        for inst in self.home.instances.all.values():
            sunrise = inst.extra.get("sunrise") or {}
            pre = float(inst.extra.get("pre_alarm_minutes", 10))
            for item in self.home.instances.items(inst, ("alarm",)):
                due = datetime.fromisoformat(item["due"])
                left = (due - now).total_seconds() / 60
                key = f"{item['id']}:{item['due']}"
                if 0 < left <= pre and key + ":soon" not in self._pre_alarm_done:
                    self._pre_alarm_done.add(key + ":soon")
                    self.home.events.log("agenda.alarm_soon", instance=inst.id, minutes=round(left),
                                         room=item.get("room"))
                mins = float(sunrise.get("minutes", 0) or 0)
                if mins and sunrise.get("light") and 0 < left <= mins and key + ":sun" not in self._pre_alarm_done:
                    self._pre_alarm_done.add(key + ":sun")
                    self.sunrise(sunrise["light"], left)

    def sunrise(self, light: dict, minutes: float, steps: int = 20) -> None:
        """Fade a dimmable light from 1% up to 100% over `minutes`."""
        light = dict(light)
        light.setdefault("backend", self.home.config.settings.default_backend)
        for i in range(steps + 1):
            level = max(1, round(100 * (i / steps) ** 2))  # slow at first, like a real sunrise
            self.home.timeline.call_in(minutes * 60 * i / steps,
                                       lambda lv=level: self.home.run_action({**light, "level": lv, "state": True}))

    def stop_ringing(self, rooms: list[str | None] | None = None) -> int:
        n = 0
        for room in list(self.ringers):
            if rooms is None or room in rooms:
                self.ringers.pop(room).stop()
                n += 1
        return n

    # ---------- schedules ----------
    def schedule_time(self, sc: dict, day) -> datetime | None:
        at = str(sc["at"]).lower()
        offset = timedelta(minutes=float(sc.get("offset", 0) or 0))
        tz = clock.now().tzinfo
        if at in ("sunrise", "sunset"):
            loc = self.home.config.location
            if "latitude" not in loc:
                log.warning("schedule %s uses %s but `location:` isn't set", sc["name"], at)
                return None
            rise, sett = clock.sun_times(day, float(loc["latitude"]), float(loc["longitude"]), tz)
            base = rise if at == "sunrise" else sett
            return None if base is None else base + offset
        t = clock.parse_hhmm(at)
        return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=tz) + offset

    def run_schedules(self, now: datetime) -> None:
        for sc in self.home.config.schedules:
            when = self.schedule_time(sc, now.date())
            if when is None or when.strftime("%H:%M") != now.strftime("%H:%M"):
                continue
            if not clock.day_matches(sc.get("days"), now.date()) or not check(sc.get("if"), self.home):
                continue
            log.info("schedule: %s", sc["name"])
            self.home.events.log("schedule", name=sc["name"])
            threading.Thread(target=self.home.run_steps, args=(sc["do"], {}, f"schedule {sc['name']}"),
                             daemon=True).start()

    # ---------- vacation: make the house look lived-in ----------
    def vacation_tick(self, now: datetime) -> None:
        v = self.home.config.vacation
        lights = v.get("lights") or []
        if not lights or time.monotonic() < self._next_vacation:
            return
        self._next_vacation = time.monotonic() + random.uniform(8, 25) * 60
        start = str(v.get("from", "sunset")).lower()
        until = str(v.get("until", "23:30"))
        if start == "sunset":
            until_t = clock.parse_hhmm(until)
            evening = clock.in_window(f"12:00-{until}", now) if until_t.hour >= 12 else \
                (now.hour >= 12 or now.time() < until_t)
            active = evening and sun_is_down(self.home, now)
        else:
            active = clock.in_window(f"{start}-{until}", now)
        for i, light in enumerate(lights):
            on = active and random.random() < 0.5
            if on == (i in self._vacation_on):
                continue
            (self._vacation_on.add if on else self._vacation_on.discard)(i)
            try:
                self.home.run_action({**light, "state": on})
            except Exception as e:
                log.warning("vacation light: %s", e)
