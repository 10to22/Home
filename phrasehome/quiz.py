"""The weekly house quiz, made from what actually happened in the house this week.

Sunday evening (or "house, quiz time"): a handful of questions in the shared rooms, a pause to shout
answers, then the answer. "next question", "stop the quiz". Questions come from the event log:
the most used command, how often the house got told to shut up, the longest shower, who left the
door unlocked, who got home latest, study minutes, parcels, the doorbell, parties...
Freak mode stays out of it unless `spicy: true`.

quiz:
  day: sun
  time: "19:00"
  questions: 6
  answer_seconds: 12
  spicy: false
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from . import clock

log = logging.getLogger("phrasehome.quiz")


def week_events(home, days: int = 7) -> list[dict]:
    since = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    evs: list[dict] = []
    path = home.events.path
    try:
        if path and Path(path).exists():
            for line in Path(path).read_text().splitlines()[-60000:]:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("time", "") >= since:
                    evs.append(e)
    except OSError:
        pass
    if not evs:
        evs = [e for e in home.events.recent if e.get("time", "") >= since]
    return evs


def _name(home, pid) -> str:
    inst = home.instances.all.get(str(pid or "").lower())
    return inst.name if inst else (str(pid).title() if pid else "someone")


def questions(home, days: int = 7, spicy: bool = False) -> list[tuple[str, str]]:
    evs = week_events(home, days)
    out: list[tuple[str, str]] = []
    cmds = Counter(e.get("name") for e in evs if e["kind"] == "command" and e.get("name"))
    if cmds:
        top, n = cmds.most_common(1)[0]
        out.append(("What was the command I heard the most this week?",
                    f"{top.replace('_', ' ')}, {n} time{'s' if n != 1 else ''}"))
    hush = cmds.get("hush", 0)
    if hush:
        out.append(("How many times did someone tell me to shut up this week?", f"{hush}. I counted. I always count"))
    life = getattr(home, "life", None)
    if life:
        rec = life.watch.shower_record(days)
        if rec:
            who = _name(home, rec[0]) if rec[0] else "someone I couldn't identify"
            out.append(("Who had the longest shower this week, and how long was it?", f"{who}, {rec[1]} minutes"))
        lo = life.watch.last_out_counts(days)
        if lo:
            who, n = max(lo.items(), key=lambda x: x[1])
            out.append(("Who left a door unlocked on their way out the most this week?",
                        f"{_name(home, who)}, {n} time{'s' if n != 1 else ''}"))
        st = life.study.week(days)
        if st:
            who, m = max(st.items(), key=lambda x: x[1])
            out.append(("Who did the most study mode this week?", f"{_name(home, who)}, with {m} minutes"))
    late = []
    for e in evs:
        if e["kind"] == "presence.arrived" and e.get("person"):
            t = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S")
            score = t.hour + t.minute / 60 + (24 if t.hour < 6 else 0)
            if score >= 21:
                late.append((score, t, e["person"]))
    if late:
        _, t, who = max(late)
        day = (t - timedelta(hours=6)).strftime("%A")
        out.append(("Who got home the latest this week?",
                    f"{_name(home, who)}, at {clock.spoken_time(t)} on {day} night"))
    parcels = sum(1 for e in evs if e["kind"] in ("package.delivered", "package.status") and
                  (e["kind"] == "package.delivered" or "deliver" in str(e.get("status", "")).lower()))
    if parcels:
        out.append(("How many parcels turned up this week?", str(parcels)))
    bell = sum(1 for e in evs if e["kind"] == "doorbell")
    if bell:
        out.append(("How many times did the doorbell ring this week?", str(bell)))
    unlocks = sum(1 for e in evs if e["kind"] == "door.unlocked")
    if unlocks >= 2:
        out.append(("How many times were the doors unlocked this week?", str(unlocks)))
    parties = sum(1 for e in evs if e["kind"] == "party.on")
    if parties:
        out.append(("How many parties did this house have this week?", str(parties)))
    asked = sum(1 for e in evs if e["kind"] in ("assistant", "ai"))
    if asked >= 3:
        out.append(("How many questions did you lot ask me this week?", str(asked)))
    if spicy:
        freaky = sum(1 for e in evs if e["kind"] == "freak.on")
        if freaky:
            out.append(("How many times was freak mode switched on this week?", f"{freaky}. No further questions"))
    return out


class Quiz:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("quiz") or {}
        self.cfg = c
        self.day = str(c.get("day", "sun"))
        self.at = str(c.get("time", "19:00"))
        self.n = int(c.get("questions", 6))
        self.gap = float(c.get("answer_seconds", 12))
        self.running = False
        self._skip = threading.Event()
        self._stop = threading.Event()
        self._last_week = ""
        self.last: list[tuple[str, str]] = []

    def rooms(self) -> list[str] | None:
        r = self.cfg.get("rooms") or self.home.instances.main.rooms
        return [str(x).lower() for x in r] or None

    def start(self, room: str | None = None, wait: bool = False) -> str:
        if self.running:
            return "the quiz is already on"
        qs = questions(self.home, 7, bool(self.cfg.get("spicy")))
        rng = random.Random(clock.now().strftime("%Y-%W"))
        rng.shuffle(qs)
        qs = qs[: self.n]
        rooms = [room] if room else self.rooms()
        if len(qs) < 2:
            self.home.voice.speak("quiz_nothing", rooms=rooms)
            return "not enough for a quiz"
        self.running = True
        self.last = qs
        self._stop.clear()
        t = threading.Thread(target=self._run, args=(qs, rooms), daemon=True)
        t.start()
        if wait:
            t.join()
        return f"quiz: {len(qs)} questions"

    def _run(self, qs: list[tuple[str, str]], rooms) -> None:
        v = self.home.voice
        try:
            self.home.events.log("quiz.started", questions=len(qs))
            v.speak("quiz_start", rooms=rooms, count=len(qs))
            for n, (q, a) in enumerate(qs, 1):
                if self._stop.is_set():
                    return
                v.speak("quiz_question", rooms=rooms, n=n, question=q)
                self._skip.clear()
                self._skip.wait(self.gap)
                if self._stop.is_set():
                    return
                v.speak("quiz_answer", rooms=rooms, answer=a)
                time.sleep(min(2.0, self.gap / 4))
            v.speak("quiz_end", rooms=rooms)
        finally:
            self.running = False

    def next(self) -> str:
        self._skip.set()
        return "next question"

    def stop(self) -> str:
        if not self.running:
            return "no quiz on"
        self._stop.set()
        self._skip.set()
        return "quiz stopped"

    def tick(self, now: datetime | None = None) -> None:
        now = now or clock.now()
        week = now.strftime("%Y-%W")
        if week == self._last_week or self.cfg.get("enabled", True) is False:
            return
        if not clock.day_matches(self.day, now.date()) or now.strftime("%H:%M") < self.at:
            return
        if now.strftime("%H:%M") > (datetime.combine(now.date(), clock.parse_hhmm(self.at)) +
                                     timedelta(hours=2)).strftime("%H:%M"):
            self._last_week = week  # missed it by hours: skip this week
            return
        self._last_week = week
        if self.home.state.home_people() or not self.home.state.presence:
            self.start()
