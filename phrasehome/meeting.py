"""House meetings: a time that works for everyone, and an agenda.

"house, call a house meeting" / "schedule a house meeting": looks over the next week for a time
when everyone's usually home (from who's been home when, these past four weeks) and nobody's
calendar, exam or quiet hours are in the way. Books it, tells everyone (phones too), reminds you
beforehand, and at the time reads out the agenda. "add the bins rota to the meeting agenda",
"what's on the agenda", "when's the house meeting", "cancel the house meeting".
The agenda also gets the week's talking points by itself (who keeps leaving the door unlocked...).

house_meeting:
  window: "18:00-22:00"     # times to try, every day
  minutes: 30
  days: 7
  room: living room
  remind: [60, 10]
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.meeting")

AGENDA = "house meeting agenda"
AWAY_WORDS = ("away", "trip", "holiday", "travel", "vacation", "camp", "flight", "conference", "home for")


class Meeting:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("house_meeting") or {}
        self.window = str(c.get("window", "18:00-22:00"))
        self.length = float(c.get("minutes", 30))
        self.days = int(c.get("days", 7))
        self.room = str(c.get("room") or (home.instances.main.rooms[0] if home.instances.main.rooms else "living room"))
        self.remind = sorted((int(x) for x in c.get("remind", [60, 10])), reverse=True)
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "life" / "meeting.json"
        self._reminded: set[str] = set()

    # ---------- the booking ----------
    def booked(self) -> dict | None:
        try:
            b = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not b or datetime.fromisoformat(b["at"]) < clock.now() - timedelta(hours=2):
            return None
        return b

    def _save(self, b: dict | None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(b or {}))

    def people(self) -> list:
        return [i for i in self.home.instances.all.values() if not i.main] or [self.home.instances.main]

    # ---------- finding a time ----------
    def candidates(self, now: datetime | None = None) -> list[datetime]:
        now = now or clock.now()
        start, end = (clock.parse_hhmm(x) for x in self.window.split("-"))
        out = []
        for d in range(self.days + 1):
            day = (now + timedelta(days=d)).date()
            t = datetime(day.year, day.month, day.day, start.hour, start.minute, tzinfo=now.tzinfo)
            last = datetime(day.year, day.month, day.day, end.hour, end.minute, tzinfo=now.tzinfo)
            while t + timedelta(minutes=self.length) <= last:
                if t > now + timedelta(hours=2):
                    out.append(t)
                t += timedelta(minutes=30)
        return out

    def busy(self, now: datetime | None = None) -> list[tuple[datetime, datetime, str]]:
        """Everyone's busy times for the next week: calendar events, exams (and the evening before)."""
        now = now or clock.now()
        out = []
        for inst in self.people():
            if inst.calendar:
                try:
                    from .calendar_feed import upcoming

                    for e in upcoming(inst.calendar, days=self.days + 1):
                        if e["all_day"]:
                            if any(w in e["summary"].lower() for w in AWAY_WORDS):
                                s = datetime.fromisoformat(e["start"]).replace(tzinfo=now.tzinfo)
                                out.append((s, s + timedelta(days=1), inst.id))
                            continue
                        s = datetime.strptime(e["start"], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
                        f = datetime.strptime(e["end"], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo) if e["end"] \
                            else s + timedelta(hours=1)
                        out.append((s, f, inst.id))
                except Exception as e:
                    log.debug("calendar for %s: %s", inst.name, e)
        life = getattr(self.home, "life", None)
        if life:
            for inst, ex in life.exams.all(self.days + 1):
                at = ex["at"]
                out.append((at.replace(hour=0, minute=0) - timedelta(hours=6), at + timedelta(hours=3), inst.id))
        return out

    def home_rate(self, weekday: int, hour: int, evs: list | None = None) -> float:
        """How often everyone was home at this weekday and hour, over the last four weeks (0-1)."""
        evs = _presence_history(self.home, 28) if evs is None else evs
        people = [i.id for i in self.people()]
        if not evs or not people:
            return 0.5
        state = {p: None for p in people}
        samples, together = 0, 0
        now = clock.now()
        start = now - timedelta(days=28)
        i = 0
        t = start.replace(minute=30, second=0, microsecond=0)
        while t < now:
            while i < len(evs) and evs[i][0] <= t:
                _, p, st = evs[i]
                if p in state:
                    state[p] = st
                i += 1
            if t.weekday() == weekday and t.hour == hour:
                samples += 1
                together += all(state[p] == "home" for p in people)
            t += timedelta(hours=1)
        return together / samples if samples else 0.5

    def find_time(self, now: datetime | None = None) -> datetime | None:
        now = now or clock.now()
        busy = self.busy(now)
        evs = _presence_history(self.home, 28)
        rates: dict[tuple[int, int], float] = {}
        best: tuple[float, datetime] | None = None
        for t in self.candidates(now):
            end = t + timedelta(minutes=self.length)
            if any(s < end and t < f for s, f, _ in busy):
                continue
            if any(i.quiet_hours and clock.in_window(i.quiet_hours, t) for i in self.people()):
                continue
            k = (t.weekday(), t.hour)
            if k not in rates:
                rates[k] = self.home_rate(*k, evs=evs)
            score = rates[k] - (t - now).days * 0.02 - abs(t.hour - 19) * 0.01
            if best is None or score > best[0]:
                best = (score, t)
        return best[1] if best else None

    def book(self, at: datetime | None = None, by: str | None = None) -> str:
        h = self.home
        at = at or self.find_time()
        if at is None:
            h.voice.speak("meeting_none_free")
            return "no time found"
        self._save({"at": at.isoformat(timespec="minutes"), "room": self.room, "by": by or ""})
        when = _when(at)
        h.events.log("meeting.booked", at=at.isoformat(timespec="minutes"))
        items = self.agenda()
        text, _ = h.voice.resolve("meeting_notify", when=when, room=self.room, items=_join(items) or "nothing yet")
        for inst in self.people():
            h.notify_person(inst, text, title="House meeting")
        h.voice.speak("meeting_booked", when=when, room=self.room)
        return f"house meeting booked for {at.strftime('%a %H:%M')}"

    def cancel(self) -> str:
        if not self.booked():
            self.home.voice.speak("meeting_none")
            return "no meeting booked"
        self._save(None)
        self.home.events.log("meeting.cancelled")
        text, _ = self.home.voice.resolve("meeting_cancelled")
        for inst in self.people():
            self.home.notify_person(inst, text, title="House meeting")
        self.home.voice.speak("meeting_cancelled")
        return "cancelled"

    def when(self) -> str:
        b = self.booked()
        if not b:
            return self.home.voice.speak("meeting_none")
        return self.home.voice.speak("meeting_when", when=_when(datetime.fromisoformat(b["at"])), room=b["room"])

    # ---------- the agenda ----------
    def agenda(self) -> list[str]:
        return self.home.instances.list_get(self.home.instances.main, AGENDA)

    def add(self, item: str) -> str:
        item = item.strip(" .")
        self.home.instances.list_add(self.home.instances.main, AGENDA, [item])
        self.home.voice.speak("meeting_agenda_added", item=item)
        return f"added {item}"

    def talking_points(self) -> list[str]:
        """The week's points the house would raise itself."""
        life = getattr(self.home, "life", None)
        out = []
        if life:
            names = {i.id: i.name for i in self.home.instances.all.values()}
            counts = life.watch.last_out_counts(30)
            if counts:
                who, n = max(counts.items(), key=lambda x: x[1])
                if n >= 2:
                    out.append(f"{names.get(who, who)} has left a door unlocked {n} times this month")
            rec = life.watch.shower_record(7)
            if rec and rec[1] >= 25:
                out.append(f"showers: {names.get(rec[0], rec[0] or 'someone')} managed {rec[1]} minutes")
        return out

    def read_agenda(self, room: str | None = None) -> str:
        items = self.agenda() + self.talking_points()
        if not items:
            return self.home.voice.speak("meeting_agenda_empty", room=room)
        return self.home.voice.speak("meeting_agenda", room=room, items=_join(items))

    # ---------- reminders ----------
    def tick(self, now: datetime | None = None) -> None:
        b = self.booked()
        if not b:
            return
        now = now or clock.now()
        at = datetime.fromisoformat(b["at"])
        left = (at - now).total_seconds() / 60
        for r in self.remind:
            key = f"{b['at']}:{r}"
            if 0 < left <= r and key not in self._reminded:
                self._reminded.add(key)
                if left > r - 3:
                    self.home.voice.speak("meeting_soon", everywhere=True, minutes=r, room=b["room"])
                    text, _ = self.home.voice.resolve("meeting_soon", minutes=r, room=b["room"])
                    for inst in self.people():
                        self.home.notify_person(inst, text, title="House meeting")
        key = f"{b['at']}:now"
        if left <= 0 and key not in self._reminded and left > -15:
            self._reminded.add(key)
            items = self.agenda() + self.talking_points()
            agenda = f"On the agenda: {_join(items)}." if items else "Nothing's on the agenda, so this should be quick."
            self.home.voice.speak("meeting_now", everywhere=True, room=b["room"], agenda=agenda)
            self.home.events.log("meeting.started")

    # ---------- what people say ----------
    def heard(self, said: str) -> str | tuple[str, dict] | None:
        import re

        s = said.lower().replace("’", "'").strip(" .!?")
        m = re.match(r"^(?:add|put) (.+?) (?:to|on|onto) the (?:house )?(?:meeting )?agenda(?: for the (?:house )?meeting)?$", s) \
            or re.match(r"^(?:add|put) (.+?) (?:to|on) the (?:house )?meeting(?: agenda)?$", s)
        if m:
            self.add(m.group(1))
            return "done"
        if re.match(r"^(?:what's|what is) on the (?:house )?(?:meeting )?agenda$|^(?:read|read out) the (?:meeting )?agenda$", s):
            self.read_agenda()
            return "done"
        return None


def _presence_history(home, days: int) -> list[tuple[datetime, str, str]]:
    """[(time, person, home|away)] from the event log, oldest first."""
    since = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    out = []
    path = home.events.path
    lines = []
    try:
        if path and Path(path).exists():
            lines = Path(path).read_text().splitlines()[-50000:]
    except OSError:
        pass
    evs = [json.loads(x) for x in lines if '"presence.' in x] if lines else list(home.events.recent)
    tz = clock.now().tzinfo
    for e in evs:
        if e.get("kind") in ("presence.arrived", "presence.left") and e.get("time", "") >= since:
            t = datetime.strptime(e["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
            out.append((t, str(e.get("person", "")).lower(), "home" if e["kind"] == "presence.arrived" else "away"))
    return sorted(out)


def _when(at: datetime) -> str:
    today = clock.now().date()
    day = "today" if at.date() == today else "tomorrow" if at.date() == today + timedelta(days=1) else at.strftime("%A")
    return f"{day} at {clock.spoken_time(at)}"


def _join(items: list[Any]) -> str:
    items = [str(i) for i in items if str(i).strip()]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
