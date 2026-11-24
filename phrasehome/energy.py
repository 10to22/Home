"""Power-monitoring smart plugs: appliance alerts, cheap-power scheduling, and who used what.

energy:
  currency: "$"
  price: 0.20                              # per kWh when no time-of-use window matches
  time_of_use:                             # optional: cheaper and pricier hours
    - { window: "23:00-07:00", price: 0.10, cheap: true }
    - { window: "16:00-21:00", price: 0.38 }
  poll_seconds: 15
  plugs:                                   # Shelly (Gen2+: kind shelly; Gen1: shelly1) or Tasmota
    washer:     { host: 192.168.1.71, kind: shelly, room: laundry, appliance: { done_below: 3 } }
    dryer:      { host: 192.168.1.72, kind: shelly, room: laundry, appliance: {} }
    dishwasher: { host: 192.168.1.73, kind: tasmota, room: kitchen, appliance: {} }
    tv:         { host: 192.168.1.74, kind: shelly, room: living room, appliance: { running_above: 30, done_after: 30 } }
    sam pc:     { host: 192.168.1.75, kind: shelly, owner: sam }

Whose energy is it? A plug's `owner`, else whoever owns the plug's room (a personal instance),
else the people detected in that room while it ran (room presence), else "household".

Events: appliance.started {appliance}, appliance.done {appliance, minutes, kwh}
Actions: {plug: dishwasher, state: on|off}   {plug: dishwasher, run: cheap, hours: 2}
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from . import clock

log = logging.getLogger("phrasehome.energy")


class Plug:
    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.host = str(cfg["host"])
        self.kind = str(cfg.get("kind", "shelly")).lower()
        self.room = str(cfg.get("room", "")).lower() or None
        self.owner = str(cfg.get("owner", "")).lower() or None
        app = cfg.get("appliance")
        self.appliance = None if app is None else {"running_above": 10.0, "done_below": 5.0,
                                                   "done_after": 120.0, "start_after": 20.0, **(app or {})}
        self.power = 0.0
        self.total_wh: float | None = None
        self.on: bool | None = None
        self.state = "idle"   # idle | starting | running | stopping
        self.since = 0.0
        self.run_started = 0.0
        self.run_wh = 0.0

    def read(self) -> None:
        if self.kind == "tasmota":
            j = requests.get(f"http://{self.host}/cm", params={"cmnd": "Status 10"}, timeout=4).json()
            e = j["StatusSNS"]["ENERGY"]
            self.power, self.total_wh = float(e.get("Power", 0)), float(e.get("Total", 0)) * 1000
            st = requests.get(f"http://{self.host}/cm", params={"cmnd": "Power"}, timeout=4).json()
            self.on = str(st.get("POWER", "")).upper() == "ON"
        elif self.kind == "shelly1":
            j = requests.get(f"http://{self.host}/status", timeout=4).json()
            m = j["meters"][0]
            self.power, self.total_wh = float(m["power"]), float(m["total"]) / 60  # watt-minutes
            self.on = bool(j["relays"][0]["ison"])
        else:
            j = requests.get(f"http://{self.host}/rpc/Switch.GetStatus", params={"id": 0}, timeout=4).json()
            self.power = float(j.get("apower", 0))
            self.total_wh = float((j.get("aenergy") or {}).get("total", 0))
            self.on = bool(j.get("output"))

    def switch(self, on: bool) -> None:
        if self.kind == "tasmota":
            requests.get(f"http://{self.host}/cm", params={"cmnd": f"Power {'On' if on else 'Off'}"}, timeout=4)
        elif self.kind == "shelly1":
            requests.get(f"http://{self.host}/relay/0", params={"turn": "on" if on else "off"}, timeout=4)
        else:
            requests.get(f"http://{self.host}/rpc/Switch.Set", params={"id": 0, "on": str(on).lower()}, timeout=4)
        self.on = on


class Energy:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("energy") or {})
        self.plugs = {str(n).lower(): Plug(str(n).lower(), dict(c)) for n, c in (self.cfg.get("plugs") or {}).items()}
        self.currency = self.cfg.get("currency", "$")
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "energy.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {"days": {}}
        self._last_save = 0.0
        self._lock = threading.Lock()
        self._running = False
        home.energy = self

    # ---------- prices ----------
    def price_at(self, t: datetime) -> tuple[float, bool]:
        for w in self.cfg.get("time_of_use") or []:
            if clock.in_window(w["window"], t):
                return float(w["price"]), bool(w.get("cheap"))
        return float(self.cfg.get("price", 0.20)), False

    def next_cheap(self, after: datetime | None = None) -> datetime | None:
        t = (after or clock.now()).replace(second=0, microsecond=0)
        if not any(w.get("cheap") for w in self.cfg.get("time_of_use") or []):
            return None
        for i in range(0, 48 * 60):
            cand = t + timedelta(minutes=i)
            if self.price_at(cand)[1]:
                return cand
        return None

    # ---------- whose energy ----------
    def attribute(self, plug: Plug) -> list[str]:
        if plug.owner:
            return [plug.owner]
        if plug.room:
            inst = self.home.instances.for_room(plug.room)
            if not inst.main:
                return [inst.id]
            rooms = getattr(self.home, "rooms", None)
            here = rooms.people_in(plug.room) if rooms else []
            if here:
                return here
        return ["household"]

    def _add(self, plug: Plug, wh: float, when: datetime) -> None:
        if wh <= 0:
            return
        kwh = wh / 1000
        price, _ = self.price_at(when)
        day = self.data["days"].setdefault(when.strftime("%Y-%m-%d"), {"devices": {}, "people": {}, "cost": 0.0})
        day["devices"][plug.name] = day["devices"].get(plug.name, 0.0) + kwh
        who = self.attribute(plug)
        for p in who:
            day["people"][p] = day["people"].get(p, 0.0) + kwh / len(who)
        day["cost"] = day.get("cost", 0.0) + kwh * price

    # ---------- polling ----------
    def update(self, plug: Plug, now: float | None = None,
               reading: tuple[float, float | None] | None = None) -> None:
        """Read a plug (or take `reading` = (watts, total Wh)) and account for it."""
        now = time.monotonic() if now is None else now
        prev_total, prev_t = plug.total_wh, getattr(plug, "_t", None)
        try:
            if reading is not None:
                plug.power, plug.total_wh = reading
            else:
                plug.read()
        except Exception as e:
            log.debug("plug %s: %s", plug.name, e)
            return
        wh = 0.0
        if prev_total is not None and plug.total_wh is not None and plug.total_wh >= prev_total:
            wh = plug.total_wh - prev_total
        elif prev_t is not None:
            wh = plug.power * (now - prev_t) / 3600
        plug._t = now
        with self._lock:
            self._add(plug, wh, clock.now())
        if plug.state in ("running", "stopping"):
            plug.run_wh += wh
        self._appliance(plug, now)

    def _appliance(self, plug: Plug, now: float) -> None:
        a = plug.appliance
        if not a:
            return
        p = plug.power
        if plug.state == "idle" and p > a["running_above"]:
            plug.state, plug.since = "starting", now
        elif plug.state == "starting":
            if p <= a["running_above"]:
                plug.state = "idle"
            elif now - plug.since >= a["start_after"]:
                plug.state, plug.run_started, plug.run_wh = "running", plug.since, 0.0
                self.home.events.log("appliance.started", appliance=plug.name, room=plug.room)
        elif plug.state == "running" and p < a["done_below"]:
            plug.state, plug.since = "stopping", now
        elif plug.state == "stopping":
            if p >= a["done_below"]:
                plug.state = "running"   # just a pause in the cycle (e.g. a washer soaking)
            elif now - plug.since >= a["done_after"]:
                plug.state = "idle"
                self.home.events.log("appliance.done", appliance=plug.name, room=plug.room,
                                     minutes=round((plug.since - plug.run_started) / 60),
                                     kwh=round(plug.run_wh / 1000, 2))

    def start(self) -> None:
        if not self.plugs:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("energy: watching %d plugs", len(self.plugs))

    def stop(self) -> None:
        self._running = False
        self.save()

    def _loop(self) -> None:
        while self._running:
            for plug in self.plugs.values():
                self.update(plug)
            if time.monotonic() - self._last_save > 300:
                self.save()
            time.sleep(float(self.cfg.get("poll_seconds", 15)))

    def save(self) -> None:
        with self._lock:
            days = self.data["days"]
            for k in sorted(days)[:-400]:
                del days[k]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data))
            tmp.replace(self.path)
            self._last_save = time.monotonic()

    # ---------- control ----------
    def plug(self, name: str) -> Plug:
        p = self.plugs.get(str(name).lower())
        if p is None:
            raise ValueError(f"no plug called '{name}' (known: {', '.join(self.plugs)})")
        return p

    def run_when_cheap(self, name: str, hours: float | None = None) -> str:
        plug = self.plug(name)
        when = self.next_cheap()
        if when is None:
            raise ValueError("no cheap hours set (energy.time_of_use with cheap: true)")
        inst = self.home.instances.main
        on = {"backend": "energy", "plug": plug.name, "state": "on"}
        if when <= clock.now() + timedelta(minutes=1):
            self.home.run_action(on)
            msg = f"Power is cheap now, so I've started the {plug.name}."
        else:
            self.home.instances.add(inst, "action", when, text=f"start {plug.name} (cheap power)", action=on)
            msg = f"I'll start the {plug.name} at {clock.spoken_time(when)}, when power is cheaper."
        if hours:
            off = {"backend": "energy", "plug": plug.name, "state": "off"}
            self.home.instances.add(inst, "action", max(when, clock.now()) + timedelta(hours=float(hours)),
                                    text=f"stop {plug.name}", action=off)
        return msg

    # ---------- reports ----------
    def totals(self, days: int = 30) -> dict[str, Any]:
        start = (clock.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        dev: dict[str, float] = {}
        ppl: dict[str, float] = {}
        cost = 0.0
        with self._lock:
            for d, v in self.data["days"].items():
                if d < start:
                    continue
                for k, x in v["devices"].items():
                    dev[k] = dev.get(k, 0) + x
                for k, x in v["people"].items():
                    ppl[k] = ppl.get(k, 0) + x
                cost += v.get("cost", 0)
        return {"devices": dict(sorted(dev.items(), key=lambda x: -x[1])),
                "people": dict(sorted(ppl.items(), key=lambda x: -x[1])),
                "kwh": sum(dev.values()), "cost": cost}

    def person_name(self, pid: str) -> str:
        inst = self.home.instances.all.get(pid)
        return inst.name if inst else pid

    def report(self, days: int = 30) -> str:
        t = self.totals(days)
        if not t["kwh"]:
            return "I don't have any energy readings yet."
        span = "today" if days == 1 else "this week" if days == 7 else f"in the last {days} days"
        top = ", ".join(f"the {k} with {v:.1f}" for k, v in list(t["devices"].items())[:3])
        people = [(self.person_name(k), v) for k, v in t["people"].items() if k != "household"]
        text = (f"The plugs I watch used {t['kwh']:.1f} kilowatt hours {span}, about "
                f"{self.currency}{t['cost']:.2f}. Biggest users: {top}.")
        if people:
            share = t["kwh"] or 1
            text += f" By person, {people[0][0]} used the most, {people[0][1]:.1f} kilowatt hours" \
                    f" ({100 * people[0][1] / share:.0f} percent)"
            text += (", then " + ", ".join(f"{n} {v:.1f}" for n, v in people[1:3])) if len(people) > 1 else ""
            text += "."
        return text
