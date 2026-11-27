"""Walk-home mode: someone's on their way home; if they don't turn up, the others hear about it.

Start it:
  - from your phone, anywhere: post to the house's ntfy topic (walk_home: {topic: ...}) in the
    ntfy app: "Parker walking home 20" (or with the title set to your name: "walking home 20",
    "from the library"). Then "Parker +10", "Parker home", "Parker cancel".
  - on the dashboard (Walk home card) or POST /api/walkhome {person, minutes}
  - by voice at home, for someone who texted: "house, Parker's walking home from the library"
When they get home (their phone joins the Wi-Fi, the door scanner sees them, they say "I'm home"),
it's done: "Parker's home safe". If they're not home `grace` minutes after they were due, everyone
gets a calm heads-up to check on them, and again `again` minutes later. Safety lines are plain in
every voice style.

walk_home:
  minutes: 20          # when no time is given
  grace: 10
  again: 20
  places: { library: 15, campus: 25, the pub: 20 }    # minutes from each
  topic: house-walk-8f2k1    # ntfy topic the house listens to (make it hard to guess)
  server: https://ntfy.sh
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.walkhome")


class WalkHome:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("walk_home") or {}
        self.cfg = c
        self.default = float(c.get("minutes", 20))
        self.grace = float(c.get("grace", 10))
        self.again = float(c.get("again", 20))
        self.places = {str(k).lower(): float(v) for k, v in (c.get("places") or {}).items()}
        self.active: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._running = False

    def _inst(self, who):
        if who is None:
            return None
        if hasattr(who, "id"):
            return who
        people = getattr(self.home, "people", None)
        if people is not None:
            return people.find(str(who))
        return next((i for i in self.home.instances.all.values() if i.matches_person(str(who))), None)

    def eta_text(self, ts: float) -> str:
        return clock.spoken_time(datetime.fromtimestamp(ts).astimezone())

    def start(self, who, minutes: float | None = None, place: str = "", by: str | None = None,
              room: str | None = None) -> str:
        inst = self._inst(who)
        if inst is None:
            raise KeyError(f"nobody called {who} lives here")
        place = (place or "").lower().strip()
        if minutes is None:
            minutes = self.places.get(place) or next((m for p, m in self.places.items() if p in place), None) \
                if place else None
        minutes = float(minutes or self.default)
        eta = time.time() + minutes * 60
        with self._lock:
            self.active[inst.id] = {"eta": eta, "by": by or "", "warned": 0, "from": place, "started": time.time()}
        self.home.events.log("walk.started", person=inst.id, minutes=minutes, place=place)
        self.home.voice.speak("walk_started", room=room, name=inst.name, eta=self.eta_text(eta))
        return f"expecting {inst.name} by {self.eta_text(eta)}"

    def extend(self, who, minutes: float) -> str:
        inst = self._inst(who)
        st = self.active.get(inst.id) if inst else None
        if not st:
            return "not walking home"
        st["eta"] = max(st["eta"], time.time()) + minutes * 60
        st["warned"] = 0
        self.home.voice.speak("walk_extended", name=inst.name, eta=self.eta_text(st["eta"]))
        return f"now expecting {inst.name} by {self.eta_text(st['eta'])}"

    def cancel(self, who) -> str:
        inst = self._inst(who)
        if not inst or self.active.pop(inst.id, None) is None:
            return "not walking home"
        self.home.events.log("walk.cancelled", person=inst.id)
        self.home.voice.speak("walk_cancelled", name=inst.name)
        return "cancelled"

    def arrived(self, who) -> bool:
        inst = self._inst(who)
        if not inst:
            return False
        with self._lock:
            st = self.active.pop(inst.id, None)
        if st is None:
            return False
        h = self.home
        h.events.log("walk.arrived", person=inst.id)
        text, _ = h.voice.resolve("walk_arrived", name=inst.name)
        tell = {st["by"]} if st["by"] and st["by"] != inst.id else set()
        if st["warned"]:  # everyone was worried: everyone hears the good news
            tell |= {i.id for i in h.instances.all.values() if not i.main and i.id != inst.id}
        for pid in tell:
            other = h.instances.all.get(pid)
            if other:
                h.notify_person(other, text, title="Home safe")
        return True

    def tick(self) -> None:
        h = self.home
        now = time.time()
        for pid, st in list(self.active.items()):
            inst = h.instances.all.get(pid)
            if inst is None:
                continue
            late = (now - st["eta"]) / 60
            key = None
            if st["warned"] == 0 and late >= self.grace:
                key = "walk_overdue"
            elif st["warned"] == 1 and late >= self.grace + self.again:
                key = "walk_overdue_again"
            if not key:
                if late > 240:
                    self.active.pop(pid, None)  # four hours on: stop
                continue
            st["warned"] += 1
            vals = dict(name=inst.name, eta=self.eta_text(st["eta"]), minutes=round(late))
            text, _ = h.voice.resolve(key, **vals)
            h.events.log("walk.overdue", person=pid, minutes=round(late))
            for other in h.instances.all.values():
                if not other.main and other.id != pid:
                    h.notify_person(other, text, title=f"{inst.name} isn't home yet")
            home_rooms = [r for p in h.state.home_people() for i in [h.instances.all.get(p)] if i for r in i.rooms[:1]]
            h.voice.speak(key, rooms=(h.instances.main.rooms + home_rooms) or None, emotion="clear", **vals)

    def on_event(self, ev: dict) -> None:
        k = ev["kind"]
        if k == "presence.arrived" or (k == "scan" and ev.get("result") in ("allowed", "match", "unlocked")):
            self.arrived(ev.get("person"))
        elif k == "person.room" and ev.get("person") in self.active:
            self.arrived(ev.get("person"))

    def status(self) -> list[dict]:
        out = []
        for pid, st in self.active.items():
            inst = self.home.instances.all.get(pid)
            out.append({"person": pid, "name": inst.name if inst else pid, "eta": self.eta_text(st["eta"]),
                        "late": st["warned"] > 0, "from": st["from"]})
        return out

    # ---------- what people say (by voice, or in the ntfy app) ----------
    def heard(self, said: str, speaker=None, room: str | None = None, title: str = "") -> str | None:
        s = said.lower().replace("’", "'").strip(" .!?")
        people = [i for i in self.home.instances.all.values() if not i.main]
        names = sorted({n for i in people for n in (i.id, i.name.lower())}, key=len, reverse=True)
        who_rx = "|".join(re.escape(n) for n in names) or r"(?!x)x"
        m = re.match(rf"^(?:({who_rx})(?:'s| is)? |(i'm|i am) )?(?:walking|heading|on (?:my|their|his|her) way|coming|"
                     rf"walking back|headed) home(?: now)?(?: from (?:the )?([a-z' ]+?))?(?:,? (?:in|about|around|"
                     rf"for|eta|be there in)? ?(\d+) ?(?:minutes?|mins?|m)?)?$", s)
        if m:
            who = m.group(1) or (title.strip().lower() if title else None)
            if who is None and m.group(2):
                who = speaker
            if who is None:
                return None
            self.start(who, float(m.group(4)) if m.group(4) else None, m.group(3) or "",
                       by=getattr(speaker, "id", None), room=room)
            return "done"
        m = re.match(rf"^({who_rx})?\s*\+ ?(\d+)(?: ?min\w*)?$", s)
        if m and (m.group(1) or title):
            self.extend(m.group(1) or title, float(m.group(2)))
            return "done"
        m = re.match(rf"^({who_rx})?\s*(?:'s |is )?(home|cancel|stop|made it|i'm home|here)$", s)
        if m and (m.group(1) or title):
            who = m.group(1) or title
            if m.group(2) in ("cancel", "stop"):
                self.cancel(who)
            else:
                self.arrived(who)
            return "done"
        if re.match(r"^(?:is )?(?:anyone|anybody|who's|who is) (?:walking|on (?:the|their) way) home$", s):
            st = self.status()
            if not st:
                self.home.voice.speak("walk_none", room=room)
            else:
                self.home.voice.speak("; ".join(f"{x['name']}, due by {x['eta']}" for x in st) + ".", room=room)
            return "done"
        return None

    # ---------- the ntfy topic ----------
    def start_listening(self) -> None:
        topic = self.cfg.get("topic")
        if not topic or self._running:
            return
        self._running = True
        threading.Thread(target=self._listen, args=(str(topic),), daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _listen(self, topic: str) -> None:
        import requests

        server = str(self.cfg.get("server", "https://ntfy.sh")).rstrip("/")
        while self._running:
            try:
                with requests.get(f"{server}/{topic}/json", stream=True, timeout=(10, 90)) as r:
                    for line in r.iter_lines():
                        if not self._running:
                            return
                        if not line:
                            continue
                        msg = json.loads(line)
                        if msg.get("event") == "message":
                            try:
                                self.heard(msg.get("message", ""), title=msg.get("title", ""))
                            except Exception as e:
                                log.warning("walk home message %r: %s", msg.get("message"), e)
            except Exception as e:
                log.debug("walk home topic: %s", e)
                time.sleep(15)
