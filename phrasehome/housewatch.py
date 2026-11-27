"""The house keeping an eye on things: the shower timer, last one out, windows left open,
the winter blues lamp, and pollen-aware window advice.

Shower timer     the bathroom humidity jumping starts it (or "shower timer"); 10 and 20 minute
                 nudges, a roast at 30, and the week's record ("who had the longest shower?").
Last one out     when the last person leaves and a door's unlocked: it locks it, tells them, and
                 tells everyone else who it was. Counted, for the house meeting and the quiz.
Windows          nobody home and a window's open: everyone's phone hears about it (and whether rain's
                 coming), again every hour; "the lounge window's still open" when someone gets back.
Winter lamp      dark winter mornings: when your alarm goes off or you get out of bed before sunrise,
                 your bright light comes on full for half an hour.
Pollen           "should I open the windows?"; a morning warning when pollen is high and someone's
                 allergic (people.py: "Parker has hay fever"), and a nudge if a window opens anyway.
                 Pollen numbers come from Open-Meteo (Europe) or Google's Pollen API (pollen: {api_key_env}).

shower:
  sensor: { node: bathroom, input: humidity }
  rise: 8            # humidity points above normal that mean someone's showering
  warn: [10, 20]
  roast: 30
  room: bathroom
last_out: { lock: true, tell: both }          # tell: person, house, both or none
windows:
  lounge window: { node: lounge, input: window }     # closed_when: on (like door contacts)
  kitchen window: { node: kitchen, input: window, room: kitchen }
winter_lamp:
  lights:
    finn's room: { node: finn, channel: lamp }
  minutes: 30
  months: auto        # Nov-Feb in the north, May-Aug in the south (from location:), or a list [6, 7, 8]
pollen:
  morning: "08:00"
  api_key_env: GOOGLE_MAPS_KEY   # optional, for pollen outside Europe
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.watch")

ALLERGY_WORDS = re.compile(r"hay ?fever|pollen|grass|birch|ragweed|asthma|allerg(?:y|ies|ic) to (?:grass|pollen|trees?)", re.I)
LEVELS = ["low", "moderate", "high", "very high"]


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


class Watch:
    def __init__(self, home) -> None:
        self.home = home
        x = home.config.extra
        self.shower_cfg = dict(x.get("shower") or {})
        self.last_out_cfg = dict(x.get("last_out") or {})
        raw = dict(x.get("windows") or {})
        self.window_opts = {k: raw.pop(k) for k in ("remind_minutes", "max_reminders") if k in raw}
        self.windows = {str(k).lower(): dict(v) for k, v in (raw.pop("sensors", None) or raw).items()
                        if isinstance(v, dict) and "node" in v}
        self.lamp_cfg = dict(x.get("winter_lamp") or {})
        self.pollen_cfg = dict(x.get("pollen") or {})
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "life" / "watch.json"
        self._lock = threading.Lock()
        self.shower: dict[str, Any] | None = None
        self._humid: deque = deque(maxlen=400)
        self._away_reminders = 0
        self._next_away = 0.0
        self._done: set[str] = set()
        self._pollen: tuple[float, tuple[str, int] | None] = (0.0, None)

    # ---------- stats ----------
    def _data(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _record(self, kind: str, row: dict) -> None:
        with self._lock:
            d = self._data()
            d.setdefault(kind, []).append({**row, "date": clock.now().strftime("%Y-%m-%d")})
            d[kind] = d[kind][-300:]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(d, indent=1))

    def _rows(self, kind: str, days: int) -> list[dict]:
        since = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        return [r for r in self._data().get(kind, []) if r.get("date", "") >= since]

    def shower_record(self, days: int = 7) -> tuple[str, int] | None:
        rows = self._rows("shower", days)
        if not rows:
            return None
        r = max(rows, key=lambda r: r["minutes"])
        return r.get("person") or "", int(r["minutes"])

    def last_out_counts(self, days: int = 30) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._rows("last_out", days):
            out[r["person"]] = out.get(r["person"], 0) + 1
        return out

    def name(self, pid: str | None) -> str:
        inst = self.home.instances.all.get(pid or "")
        return inst.name if inst else (pid or "someone").title()

    # ================================================================ shower timer
    def shower_room(self) -> str | None:
        return str(self.shower_cfg.get("room") or "bathroom").lower()

    def shower_start(self, person: str | None = None, manual: bool = True) -> str:
        if self.shower:
            return "the shower timer's already going"
        rooms = getattr(self.home, "rooms", None)
        if person is None and rooms is not None:
            try:
                here = rooms.people_in(self.shower_room())
                person = here[0] if len(here) == 1 else None
            except Exception:
                person = None
        self.shower = {"start": time.time(), "person": person, "manual": manual, "said": []}
        self.home.events.log("shower.on", person=person or "")
        if manual:
            self.home.voice.speak("shower_started", room=self.shower_room())
        return "shower timer on"

    def shower_stop(self, why: str = "") -> str:
        st, self.shower = self.shower, None
        if not st:
            return "no shower timer going"
        minutes = round((time.time() - st["start"]) / 60)
        warn = sorted(int(w) for w in self.shower_cfg.get("warn", [10, 20]))
        if minutes >= 2:
            self._record("shower", {"person": st["person"] or "", "minutes": minutes})
            self.home.events.log("shower.off", person=st["person"] or "", minutes=minutes)
        if minutes >= (warn[0] if warn else 10) or st["manual"]:
            self.home.voice.speak("shower_done", room=self.shower_room(), minutes=minutes)
        return f"shower over: {minutes} minutes"

    def _shower_sensor(self, ev: dict) -> None:
        s = self.shower_cfg.get("sensor") or {}
        if not s or ev.get("node") != str(s.get("node", "")).lower() or ev.get("input") != str(s.get("input", "")).lower():
            return
        v = ev.get("value")
        if v is None:
            return
        now = time.time()
        self._humid.append((now, float(v)))
        recent = [x for t, x in self._humid if now - t <= 1800]
        base = min(recent) if recent else float(v)
        rise = float(self.shower_cfg.get("rise", 8))
        above = self.shower_cfg.get("above")
        if not self.shower and (float(v) - base >= rise or (above is not None and float(v) >= float(above))):
            self.shower_start(manual=False)
            self.shower["base"] = base
        elif self.shower and not self.shower["manual"] and float(v) <= self.shower.get("base", base) + rise * 0.5:
            self.shower_stop("humidity back down")

    def _shower_tick(self) -> None:
        st = self.shower
        if not st:
            return
        minutes = (time.time() - st["start"]) / 60
        room = self.shower_room()
        for w in sorted(int(x) for x in self.shower_cfg.get("warn", [10, 20])):
            if minutes >= w and w not in st["said"]:
                st["said"].append(w)
                if minutes < w + 2:
                    self.home.voice.speak("shower_warn", room=room, minutes=w)
        roast = int(self.shower_cfg.get("roast", 30))
        for r in (roast, roast + 15):
            if minutes >= r and r not in st["said"]:
                st["said"].append(r)
                if minutes < r + 2:
                    self.home.voice.speak("shower_roast", room=room, minutes=r)
        if minutes >= float(self.shower_cfg.get("max", 90)):
            self.shower_stop("too long: probably not a shower")

    def say_shower_record(self, room: str | None = None) -> str:
        rec = self.shower_record(7)
        if not rec:
            return self.home.voice.speak("shower_none", room=room)
        return self.home.voice.speak("shower_record", room=room, name=self.name(rec[0]), minutes=rec[1])

    # ================================================================ last one out
    def _everyone_left(self, ev: dict) -> None:
        h = self.home
        cfg = self.last_out_cfg
        if cfg.get("enabled", True) is False:
            return
        pid = str(ev.get("person") or "")
        inst = h.instances.all.get(pid) or next((i for i in h.instances.all.values() if i.matches_person(pid)), None)
        try:
            unlocked = [d for d in h.doors.names() if h.doors.state(d) == "unlocked"]
        except Exception as e:
            log.debug("last out: %s", e)
            unlocked = []
        if cfg.get("lights_off"):
            threading.Thread(target=h.run_steps, args=(cfg["lights_off"], {}, "last one out"), daemon=True).start()
        if not unlocked:
            return
        failed = []
        if cfg.get("lock", True):
            for d in unlocked:
                try:
                    h.doors.set(d, "locked", reason=f"last out: {pid}")
                except Exception as e:
                    log.warning("last out: couldn't lock the %s door: %s", d, e)
                if h.doors.state(d) != "locked":
                    failed.append(d)
        doors = _join([f"{d} door" for d in unlocked])
        name = inst.name if inst else (pid.title() or "Someone")
        self._record("last_out", {"person": inst.id if inst else pid, "doors": unlocked})
        count = self.last_out_counts(30).get(inst.id if inst else pid, 1)
        h.events.log("last_out", person=inst.id if inst else pid, doors=",".join(unlocked), locked=not failed)
        nth = {1: "once", 2: "twice"}.get(count, f"{count} times")
        others = [i for i in h.instances.all.values() if not i.main and (inst is None or i.id != inst.id)]
        if failed:  # safety first: the plain words, to everyone
            text, _ = h.voice.resolve("last_out_couldnt", name=name, doors=_join([f"{d} door" for d in failed]))
            for i in ([inst] if inst else []) + others:
                h.notify_person(i, text, title="Door unlocked")
            return
        tell = str(cfg.get("tell", "both")).lower()
        if inst is not None and tell in ("both", "person"):
            text, _ = h.voice.resolve("last_out_locked", name=name, doors=doors, count=nth)
            h.notify_person(inst, text, title="You left it unlocked")
        if tell in ("both", "house"):
            text, _ = h.voice.resolve("last_out_shame", name=name, doors=doors, count=nth)
            for i in others:
                h.notify_person(i, text, title="Last one out")

    # ================================================================ windows
    def open_windows(self) -> list[str]:
        s = self.home.sensors
        if not s:
            return []
        out = []
        for name, w in self.windows.items():
            cur = s.current(w["node"], w["input"])
            if cur is None:
                continue
            closed_when = str(w.get("closed_when", "on")).lower()
            if cur.get("state") != closed_when:
                out.append(name)
        return out

    def _windows_away(self, first: bool = False) -> None:
        h = self.home
        if h.state.home_people():
            return
        open_ = self.open_windows()
        if not open_:
            return
        rain = ""
        w = getattr(h, "weather", None)
        if w is not None:
            chance = (w.day(0) or {}).get("precipitation_probability_max")
            if chance is not None and chance >= 40:
                rain, _ = h.voice.resolve("windows_rain", chance=round(chance))
        text, _ = h.voice.resolve("windows_open_away", windows=_join(open_), is_are="is" if len(open_) == 1 else "are",
                                  rain=rain)
        for i in h.instances.all.values():
            if not i.main:
                h.notify_person(i, text, title="Window open")
        h.events.log("windows.open_away", windows=",".join(open_))
        self._away_reminders = 1 if first else self._away_reminders + 1
        self._next_away = time.time() + float(self.window_opts.get("remind_minutes", 60)) * 60

    def _windows_tick(self) -> None:
        if not self.windows or self.home.state.home_people() or not self._next_away:
            return
        if time.time() >= self._next_away and self._away_reminders < int(self.window_opts.get("max_reminders", 3)):
            self._windows_away()

    def _window_sensor(self, ev: dict) -> None:
        for name, w in self.windows.items():
            if ev.get("node") == str(w["node"]).lower() and ev.get("input") == str(w["input"]).lower():
                opened = ev.get("state") != str(w.get("closed_when", "on")).lower()
                if not opened:
                    return
                if not self.home.state.home_people() and not self._next_away:
                    self._windows_away(first=True)
                    return
                lvl = self.pollen_level()
                key = f"pollen-window:{name}:{clock.now().date()}"
                if lvl and lvl[1] >= 2 and key not in self._done:
                    self._done.add(key)
                    room = w.get("room") or (self.home.instances.main.rooms[0] if self.home.instances.main.rooms else None)
                    who = self.allergic()
                    self.home.voice.speak("pollen_window_opened", room=room, level=lvl[0],
                                          who=f", and {_join([i.name for i in who])} {'has' if len(who) == 1 else 'have'} hay fever" if who else "")
                return

    # ================================================================ winter lamp
    def winter(self, now: datetime | None = None) -> bool:
        now = now or clock.now()
        months = self.lamp_cfg.get("months", "auto")
        if months == "auto" or not months:
            lat = float(self.home.config.location.get("latitude", 40))
            months = [5, 6, 7, 8] if lat < 0 else [11, 12, 1, 2]
        return now.month in [int(m) for m in months]

    def _lamp_for(self, room: str | None):
        lights = {str(k).lower(): v for k, v in (self.lamp_cfg.get("lights") or {}).items()}
        if room and room.lower() in lights:
            return room.lower(), lights[room.lower()]
        for inst in self.home.instances.all.values():
            if inst.extra.get("winter_lamp") and room and room.lower() in inst.rooms:
                return room.lower(), inst.extra["winter_lamp"]
        if self.lamp_cfg.get("light") and room:
            return room.lower(), self.lamp_cfg["light"]
        return None

    def winter_lamp(self, room: str | None, now: datetime | None = None) -> bool:
        if not self.lamp_cfg and not any(i.extra.get("winter_lamp") for i in self.home.instances.all.values()):
            return False
        now = now or clock.now()
        found = self._lamp_for(room)
        if not found or not self.winter(now):
            return False
        start, end = str(self.lamp_cfg.get("from", "04:00")), str(self.lamp_cfg.get("until", "10:30"))
        if not clock.in_window(f"{start}-{end}", now):
            return False
        from .automation import sun_is_down

        if not sun_is_down(self.home, now):
            return False
        room, light = found
        key = f"lamp:{room}:{now.date()}"
        if key in self._done:
            return False
        self._done.add(key)
        minutes = float(self.lamp_cfg.get("minutes", 30))
        action = {**light, "state": True, "level": int(self.lamp_cfg.get("level", 100))}
        action.setdefault("backend", self.home.config.settings.default_backend)
        try:
            self.home.run_action(action, {"here": room})
        except Exception as e:
            log.warning("winter lamp: %s", e)
            return False
        self.home.timeline.call_in(minutes * 60, lambda: self.home.run_action({**action, "state": False}, {"here": room}))
        self.home.events.log("winter_lamp", room=room)
        self.home.voice.speak("winter_lamp_on", room=room, minutes=round(minutes))
        return True

    # ================================================================ pollen
    def pollen_level(self) -> tuple[str, int] | None:
        """(words, 0-3) for today's highest pollen, or None if there's no pollen data here."""
        if time.monotonic() - self._pollen[0] < 1800 and self._pollen[0]:
            return self._pollen[1]
        lvl = None
        w = getattr(self.home, "weather", None)
        cur = (getattr(w, "air", None) or {}).get("current") or {}
        from .weather import POLLEN

        vals = [v for k, v in cur.items() if k in POLLEN and v is not None]
        if vals:
            v = max(vals)
            idx = 3 if v > 200 else 2 if v > 50 else 1 if v > 10 else 0
            lvl = (LEVELS[idx], idx)
        key = os.environ.get(str(self.pollen_cfg.get("api_key_env", "GOOGLE_MAPS_KEY")), "")
        loc = self.home.config.location
        if lvl is None and key and "latitude" in loc:
            try:
                import requests

                r = requests.get("https://pollen.googleapis.com/v1/forecast:lookup", timeout=10, params={
                    "key": key, "location.latitude": loc["latitude"], "location.longitude": loc["longitude"], "days": 1})
                info = (r.json().get("dailyInfo") or [{}])[0].get("pollenTypeInfo") or []
                upi = max((t.get("indexInfo", {}).get("value", 0) for t in info), default=None)
                if upi is not None:
                    idx = 3 if upi >= 5 else 2 if upi >= 4 else 1 if upi >= 2 else 0
                    lvl = (LEVELS[idx], idx)
            except Exception as e:
                log.debug("google pollen: %s", e)
        self._pollen = (time.monotonic(), lvl)
        return lvl

    def allergic(self) -> list:
        people = getattr(self.home, "people", None)
        out = []
        for inst in self.home.instances.all.values():
            if inst.main:
                continue
            facts = " ".join(people.facts(inst)) if people else ""
            diet = self.home.instances.diet(inst, inst.diet)
            if ALLERGY_WORDS.search(facts + " " + " ".join(diet.get("allergies", []))):
                out.append(inst)
        return out

    def window_advice(self, room: str | None = None) -> str:
        h = self.home
        w = getattr(h, "weather", None)
        cur = (getattr(w, "air", None) or {}).get("current") or {}
        aqi = cur.get("us_aqi")
        if aqi is not None and aqi > 100:
            from .weather import aqi_words

            return h.voice.speak("air_bad", room=room, aqi=aqi_words(aqi))
        lvl = self.pollen_level()
        who = self.allergic()
        if lvl and lvl[1] >= 2:
            names = _join([i.name for i in who])
            return h.voice.speak("pollen_high", room=room, level=lvl[0],
                                 who=f" Especially for {names}." if who else "")
        why = []
        if lvl:
            why.append(f"Pollen's {lvl[0]}")
        now_ = (getattr(w, "data", None) or {}).get("current") or {}
        rain = ((w.day(0) or {}).get("precipitation_probability_max") if w else None)
        if rain is not None and rain >= 60:
            why.append(f"but there's a {round(rain)} percent chance of rain, so not for long")
        if now_.get("temperature_2m") is not None:
            why.append(f"it's {round(now_['temperature_2m'])} degrees out")
        if not why:
            return h.voice.speak("pollen_unknown", room=room)
        return h.voice.speak("pollen_ok", room=room, why=", ".join(why).capitalize() + ".")

    def _pollen_tick(self, now: datetime) -> None:
        if not self.pollen_cfg and not self.allergic():
            return
        t = clock.parse_hhmm(str(self.pollen_cfg.get("morning", "08:00")))
        key = f"pollen-morning:{now.date()}"
        if (now.hour, now.minute) < (t.hour, t.minute) or key in self._done:
            return
        self._done.add(key)
        lvl = self.pollen_level()
        who = self.allergic()
        if not lvl or lvl[1] < 2 or not who:
            return
        names = _join([i.name for i in who])
        self.home.voice.speak("pollen_high", rooms=self.home.instances.main.rooms or None, level=lvl[0],
                              who=f" Especially for {names}.")
        text, _ = self.home.voice.resolve("pollen_high", level=lvl[0], who="")
        for i in who:
            self.home.notify_person(i, text, title="Pollen")

    # ================================================================ plumbing
    def on_event(self, ev: dict) -> None:
        k = ev["kind"]
        if k == "sensor":
            if self.shower_cfg:
                self._shower_sensor(ev)
            if self.windows and "state" in ev:
                self._window_sensor(ev)
        elif k == "presence.everyone_left":
            self._everyone_left(ev)
            if self.windows:
                self._next_away = 0.0
                self._windows_away(first=True)
        elif k == "presence.first_home":
            self._next_away, self._away_reminders = 0.0, 0
            open_ = self.open_windows()
            if open_:
                self.home.voice.speak("windows_still_open", rooms=self.home.instances.main.rooms or None,
                                      windows=_join(open_), is_are="is" if len(open_) == 1 else "are")
        elif k == "agenda.alarm":
            self.winter_lamp(ev.get("room"))
        elif k == "bed.out":
            inst = self.home.instances.all.get(str(ev.get("person", "")))
            if inst and inst.rooms:
                self.winter_lamp(inst.rooms[0])

    def tick(self, now: datetime | None = None) -> None:
        now = now or clock.now()
        self._shower_tick()
        self._windows_tick()
        try:
            self._pollen_tick(now)
        except Exception as e:
            log.debug("pollen: %s", e)
