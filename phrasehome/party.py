"""Party planner.

"party mode" / "start the party" / "let's party":
  - your `party` routine runs (lights, whatever you put in it), and the playlist starts on Spotify
  - the guest Wi-Fi is switched on (your router's webhook, if you have one) and said out loud
  - a quiet-time countdown: 60, 30 and 10 minutes out, then the music goes down and `at_quiet` runs.
    Quiet time comes earlier when someone has an exam the next day (study.py).
  - "party's over" (or a few hours after quiet time): cleanup jobs are shared out for the next day,
    as reminders on everyone's phone and in their room, taking turns week to week
"what's the wifi password" / "when's quiet time" while it's on.

party:
  routine: party                 # default: your `party` routine if there is one
  playlist: "house party"        # Spotify
  volume: 70
  guest_wifi: { ssid: Party Time, password: letsgo123, on: [ { backend: router, url: "..." } ] }
  quiet_time: { weekdays: "22:30", weekends: "00:30" }   # or just "23:00"
  warn: [60, 30, 10]
  at_quiet: [ { node: lounge, channel: lamp, level: 40 } ]
  quiet_volume: 25
  cleanup: [bottles and recycling, kitchen benches and dishes, vacuum the lounge, bins out]
  cleanup_time: "11:00"
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.party")

DEFAULT_CLEANUP = ["bottles, cans and recycling", "kitchen benches and dishes", "vacuum the lounge",
                   "take the bins out and check the bathroom"]


class Party:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("party") or {}
        self.cfg = c
        routines = home.config.routines
        self.routine = c.get("routine", "party" if "party" in routines else None)
        self.end_routine = c.get("end_routine", "party_over" if "party_over" in routines else None)
        self.active: dict[str, Any] | None = None
        self._lock = threading.Lock()

    # ---------- starting and ending ----------
    def start(self, room: str | None = None, by: str | None = None, run_routine: bool = True) -> str:
        h = self.home
        with self._lock:
            if self.active:
                return "the party's already on"
            quiet, why = self.quiet_time()
            self.active = {"room": room or h.default_room(), "by": by, "started": time.time(),
                           "quiet_at": quiet.timestamp(), "why": why, "warned": [], "quieted": False}
        h.events.log("party.on", room=room, quiet=quiet.strftime("%H:%M"))
        extras = []
        if run_routine and self.routine and self.routine in h.config.routines:
            threading.Thread(target=h.run_steps, args=(h.config.routines[self.routine], {"here": room or ""},
                                                       "party mode"), daemon=True).start()
        if self.cfg.get("playlist") and getattr(h, "spotify", None):
            try:
                h.spotify.play(playlist=str(self.cfg["playlist"]))
                if self.cfg.get("volume"):
                    h.spotify.simple("volume", self.cfg["volume"])
                extras.append("Playlist's going.")
            except Exception as e:
                log.warning("party playlist: %s", e)
        wifi = self.cfg.get("guest_wifi") or {}
        if wifi.get("on"):
            threading.Thread(target=h.run_steps, args=(wifi["on"], {}, "guest wifi"), daemon=True).start()
        if wifi.get("ssid"):
            extras.append(f"Guest Wi-Fi is {wifi['ssid']}" + (f", password {wifi['password']}." if wifi.get("password") else "."))
        extras.append(f"Quiet time is {clock.spoken_time(quiet)}" + (f", because {why}." if why else "."))
        h.voice.speak("party_on", room=room, extras=" ".join(extras))
        return f"party on, quiet time {quiet.strftime('%H:%M')}"

    def stop(self, why: str = "") -> str:
        h = self.home
        with self._lock:
            st, self.active = self.active, None
        if st is None:
            h.voice.speak("party_not_on")
            return "no party on"
        wifi = self.cfg.get("guest_wifi") or {}
        if wifi.get("off"):
            threading.Thread(target=h.run_steps, args=(wifi["off"], {}, "guest wifi off"), daemon=True).start()
        if self.end_routine and self.end_routine in h.config.routines:
            threading.Thread(target=h.run_steps, args=(h.config.routines[self.end_routine], {}, "party over"),
                             daemon=True).start()
        jobs = self.share_cleanup()
        h.events.log("party.off", why=why or "ended", jobs=len(jobs))
        cleanup = (f"Cleanup's tomorrow at {clock.spoken_time(self._cleanup_at())}: " +
                   "; ".join(f"{n}, {t}" for n, t in jobs) + ".") if jobs else ""
        h.voice.speak("party_off", room=st.get("room"), cleanup=cleanup)
        return "party over"

    # ---------- quiet time ----------
    def quiet_time(self, now: datetime | None = None) -> tuple[datetime, str]:
        now = now or clock.now()
        q = self.cfg.get("quiet_time", "23:00")
        if isinstance(q, dict):
            weekend = now.weekday() in (4, 5)  # Friday and Saturday nights
            q = q.get("weekends" if weekend else "weekdays", "23:00")
        t = clock.parse_hhmm(str(q))
        at = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if t.hour < 12 and now.hour >= 12:
            at += timedelta(days=1)  # "00:30" is after midnight
        elif at <= now - timedelta(hours=6):
            at += timedelta(days=1)
        why = ""
        life = getattr(self.home, "life", None)
        exam = life.exams.quiet_time(now) if life else None
        if exam and exam[0] < at:
            who, has = life.exams.names(exam[1])
            at, why = max(exam[0], now + timedelta(minutes=30)), f"{who} {has} an exam tomorrow"
        return at, why

    def tick(self) -> None:
        st = self.active
        if not st:
            return
        h = self.home
        left = (st["quiet_at"] - time.time()) / 60
        for w in sorted((int(x) for x in self.cfg.get("warn", [60, 30, 10])), reverse=True):
            if left <= w and w not in st["warned"] and left > 0:
                st["warned"].append(w)
                if left > w - 2:  # don't announce a warning that's long gone
                    h.voice.speak("party_quiet_soon", room=st.get("room"), minutes=w)
        if left <= 0 and not st["quieted"]:
            st["quieted"] = True
            self.quiet_down(st)
        if left < -180:
            self.stop("three hours after quiet time")

    def quiet_down(self, st: dict) -> None:
        h = self.home
        h.events.log("party.quiet")
        if getattr(h, "spotify", None):
            try:
                h.spotify.simple("volume", int(self.cfg.get("quiet_volume", 25)))
            except Exception as e:
                log.debug("party quiet volume: %s", e)
        if self.cfg.get("at_quiet"):
            threading.Thread(target=h.run_steps, args=(self.cfg["at_quiet"], {"here": st.get("room") or ""},
                                                       "quiet time"), daemon=True).start()
        h.voice.speak("party_quiet_now", room=st.get("room"))

    # ---------- the morning after ----------
    def _cleanup_at(self) -> datetime:
        now = clock.now()
        t = clock.parse_hhmm(str(self.cfg.get("cleanup_time", "11:00")))
        day = now.date() + timedelta(days=0 if now.hour < 6 else 1)
        return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=now.tzinfo)

    def share_cleanup(self) -> list[tuple[str, str]]:
        h = self.home
        tasks = [str(t) for t in (self.cfg.get("cleanup") or DEFAULT_CLEANUP)]
        people = [i for i in h.instances.all.values() if not i.main] or [h.instances.main]
        if not tasks or self.cfg.get("cleanup") is False:
            return []
        turn = clock.now().isocalendar()[1]  # a different job each week
        at = self._cleanup_at()
        jobs: list[tuple[str, str]] = []
        for n, task in enumerate(tasks):
            inst = people[(n + turn) % len(people)]
            text, _ = h.voice.resolve("party_cleanup", task=task, name=inst.name)
            h.instances.add(inst, "reminder", at, text=f"Party cleanup: {task}")
            h.notify_person(inst, f"{text} (tomorrow at {clock.spoken_time(at)})", title="Party cleanup")
            jobs.append((inst.name, task))
        return jobs

    # ---------- questions while it's on ----------
    def wifi(self) -> str:
        w = self.cfg.get("guest_wifi") or {}
        if not w.get("ssid"):
            return ""
        return self.home.voice.speak("party_wifi", ssid=w["ssid"], password=w.get("password", "none"))

    def quiet_when(self, room: str | None = None) -> str:
        if self.active:
            at = datetime.fromtimestamp(self.active["quiet_at"]).astimezone()
        else:
            at, _ = self.quiet_time()
        left = clock.spoken_duration(max(0, at.timestamp() - time.time()))
        return self.home.voice.speak("party_quiet_when", room=room, quiet=clock.spoken_time(at), left=left)

    def on_event(self, ev: dict) -> None:
        if ev["kind"] == "routine" and self.routine and ev.get("name") == self.routine and not self.active:
            self.start(run_routine=False)  # your own "party mode" command ran the routine: plan the rest

    def status(self) -> dict | None:
        st = self.active
        if not st:
            return None
        return {"quiet_at": datetime.fromtimestamp(st["quiet_at"]).strftime("%H:%M"), "why": st["why"],
                "minutes_left": max(0, round((st["quiet_at"] - time.time()) / 60)), "quieted": st["quieted"]}
