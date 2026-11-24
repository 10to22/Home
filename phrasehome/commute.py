"""Personal traffic and "leave now" nudges.

commute:
  api_key_env: GOOGLE_MAPS_KEY       # Google Routes API (has a free monthly allowance)
  home: "123 Main St, Springfield"
  buffer_minutes: 5
instances:
  sam:
    commute: { to: "Acme Corp, 500 Market St", arrive_by: "09:00", days: weekdays, mode: drive }
  mia:
    commute: { to: "Lincoln Elementary", arrive_by: "08:15", days: weekdays, mode: walk, minutes: 15 }
  # from_calendar: true -> on days with a calendar event that has a location, head there instead

Each morning it checks live traffic, works out when to leave, and nudges the person in whatever
room they're in: "Sam, leave in 10 minutes: traffic is heavy, 34 minutes to Acme." Then "time to
go". Events commute.leave_soon / commute.leave_now (hook car pre-heating to leave_soon).
Without an API key it uses the fixed `minutes:` travel time.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any

import requests

from . import clock

log = logging.getLogger("phrasehome.commute")

ROUTES = "https://routes.googleapis.com/directions/v2:computeRoutes"
MODES = {"drive": "DRIVE", "transit": "TRANSIT", "bike": "BICYCLE", "walk": "WALK"}


class Commute:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("commute") or {})
        self.key = self.cfg.get("api_key") or os.environ.get(self.cfg.get("api_key_env", "GOOGLE_MAPS_KEY"), "")
        self.buffer = float(self.cfg.get("buffer_minutes", 5))
        self.plans: dict[str, dict[str, Any]] = {}   # person -> today's plan
        self._running = False
        home.commute = self

    def people(self) -> list:
        return [i for i in self.home.instances.all.values() if i.extra.get("commute")]

    # ---------- travel time ----------
    def travel(self, to: str, mode: str = "drive") -> dict[str, Any]:
        """{minutes, usual_minutes, traffic: light|moderate|heavy|unknown}"""
        if not self.key:
            raise RuntimeError("no Google Maps key")
        body: dict[str, Any] = {"origin": {"address": self.cfg.get("home", "")}, "destination": {"address": to},
                                "travelMode": MODES.get(mode, "DRIVE")}
        if body["travelMode"] == "DRIVE":
            body["routingPreference"] = "TRAFFIC_AWARE"
        r = requests.post(ROUTES, json=body, timeout=15, headers={
            "X-Goog-Api-Key": self.key, "X-Goog-FieldMask": "routes.duration,routes.staticDuration"})
        r.raise_for_status()
        route = (r.json().get("routes") or [{}])[0]
        mins = float(str(route.get("duration", "0s")).rstrip("s")) / 60
        usual = float(str(route.get("staticDuration", route.get("duration", "0s"))).rstrip("s")) / 60
        return {"minutes": round(mins), "usual_minutes": round(usual), "traffic": traffic_word(mins, usual)}

    def destination(self, inst, today: datetime) -> tuple[str, datetime] | None:
        c = inst.extra["commute"]
        if c.get("from_calendar") and inst.calendar:
            try:
                from .calendar_feed import upcoming

                for ev in upcoming(inst.calendar, 1):
                    if ev.get("location") and not ev["all_day"]:
                        start = datetime.strptime(ev["start"], "%Y-%m-%d %H:%M").replace(tzinfo=today.tzinfo)
                        if start > today:
                            return ev["location"], start - timedelta(minutes=float(c.get("early_minutes", 10)))
            except Exception as e:
                log.debug("calendar for commute: %s", e)
        if not clock.day_matches(c.get("days", "weekdays"), today.date()):
            return None
        t = clock.parse_hhmm(c["arrive_by"])
        return c["to"], today.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)

    def plan(self, inst, now: datetime | None = None) -> dict[str, Any] | None:
        now = now or clock.now()
        dest = self.destination(inst, now)
        if not dest:
            return None
        to, arrive = dest
        c = inst.extra["commute"]
        try:
            t = self.travel(to, c.get("mode", "drive"))
        except Exception as e:
            log.debug("traffic for %s: %s", inst.id, e)
            m = float(c.get("minutes", 25))
            t = {"minutes": round(m), "usual_minutes": round(m), "traffic": "unknown"}
        leave = arrive - timedelta(minutes=t["minutes"] + self.buffer)
        p = {"person": inst.id, "name": inst.name, "to": c.get("label", to), "arrive": arrive, "leave": leave, **t}
        old = self.plans.get(inst.id) or {}
        p["nudged"] = old.get("nudged", set()) if old.get("arrive") == arrive else set()
        self.plans[inst.id] = p
        return p

    def summary(self, inst) -> str:
        p = self.plans.get(inst.id) or self.plan(inst)
        if not p:
            return f"{inst.name} has nowhere to be today."
        traffic = "" if p["traffic"] == "unknown" else f"Traffic is {p['traffic']}: "
        return (f"{traffic}{p['minutes']} minutes to {p['to']}. Leave by {clock.spoken_time(p['leave'])} "
                f"to get there by {clock.spoken_time(p['arrive'])}.")

    # ---------- nudges ----------
    def tick(self, now: datetime | None = None) -> None:
        """Only checks traffic in the 3 hours before each person needs to arrive (every 10 min),
        so a day costs a handful of traffic requests, not hundreds."""
        now = now or clock.now()
        for inst in self.people():
            dest = self.destination(inst, now)  # no traffic request: config / cached calendar
            if not dest:
                continue
            _, arrive = dest
            if not (arrive - timedelta(hours=3) <= now <= arrive):
                continue
            if self.home.state.presence and inst.id not in self.home.state.home_people():
                continue  # already out
            p = self.plans.get(inst.id)
            if not p or p["arrive"] != arrive or (now - p.get("_at", now - timedelta(hours=1))).total_seconds() >= 600:
                p = self.plan(inst, now)
                if not p:
                    continue
                p["_at"] = now
            to_go = (p["leave"] - now).total_seconds() / 60
            for mark, key in ((10, "leave_soon"), (0, "leave_now")):
                if to_go <= mark and key not in p["nudged"] and to_go > mark - 15:
                    p["nudged"].add(key)
                    self.nudge(inst, p, key, max(0, round(to_go)))

    def nudge(self, inst, p: dict, key: str, minutes: int) -> None:
        traffic = "" if p["traffic"] in ("unknown", "light") else f" Traffic is {p['traffic']}."
        if key == "leave_soon":
            text = f"{inst.name}, leave in {minutes} minutes for {p['to']}.{traffic} It's {p['minutes']} minutes away."
        else:
            text = f"{inst.name}, time to leave for {p['to']}.{traffic}"
        w = self.home.weather.needs() if getattr(self.home, "weather", None) else []
        if w and key == "leave_soon":
            text += " Take " + " and ".join("an umbrella" if x == "umbrella" else "a jacket" for x in w) + "."
        self.home.events.log(f"commute.{key}", person=inst.id, minutes=minutes, travel=p["minutes"],
                             traffic=p["traffic"], to=p["to"])
        room = None
        rooms = getattr(self.home, "rooms", None)
        if rooms:
            w = rooms.where(inst.id)
            room = w[0] if w else None
        self.home.voice.speak(text, room=room or (inst.rooms[0] if inst.rooms else None))

    def start(self) -> None:
        if not self.people():
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception as e:
                log.warning("commute: %s", e)
            time.sleep(30)


def traffic_word(minutes: float, usual: float) -> str:
    if usual <= 0:
        return "unknown"
    r = minutes / usual
    return "heavy" if r > 1.35 else "moderate" if r > 1.12 else "light"
