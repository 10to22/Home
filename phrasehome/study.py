"""Study mode, exam-aware quiet hours, and door status lights.

Study mode (per room)    "study mode" / "focus mode" / "I'm studying": focus blocks with breaks
                         (25 on, 5 off, a long break every 4), the lamp the way you study best, no
                         announcements in there, and your door light says "studying". "skip the break",
                         "how long left", "study mode off" (tells you how much you did).
Exams                    each person's exams, from their calendar (anything called exam, final, midterm,
                         test or quiz), from config, or said: "house, I've got a calc exam on Friday at 9".
                         The night before, everyone's told (phones, and in the shared rooms), the whole
                         house goes quiet from `quiet_from`, parties end earlier, and on the day they get
                         a good luck.
Door status lights       an LED by each bedroom door: studying, sleeping, busy (do not disturb, freak
                         mode: no details) or free. Worked out by itself, or "set my door to busy".

study:
  focus: 25          # minutes
  break: 5
  long_break: 15
  long_every: 4
  light: [ { node: "{here}", channel: lamp, level: 100 } ]      # while studying
  light_after: [ { node: "{here}", channel: lamp, level: 60 } ]
exams:
  quiet_from: "21:30"    # the night before anyone's exam
  heads_up: "19:00"      # when everyone's told
  good_luck: "07:30"     # if no alarm went off first
door_status:
  rooms:
    finn's room: { node: finn_door }          # LEDs on channels red, green, blue
    parker's room: { node: parker_door, channels: [r, g, b] }
  colors: { studying: [red, green], sleeping: [blue], busy: [red], free: [green] }
instances:
  parker:
    exams: [ { subject: contract law, at: "2026-12-04 09:00" } ]
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.study")

EXAM_WORDS = re.compile(r"\b(exam|final|finals|midterm|mid-term|test|quiz|assessment|viva)\b", re.I)
STATUSES = ("studying", "sleeping", "busy", "free")
STATUS_WORDS = {"studying": "studying", "study": "studying", "focus": "studying", "focusing": "studying",
                "sleeping": "sleeping", "asleep": "sleeping", "sleep": "sleeping", "napping": "sleeping",
                "busy": "busy", "do not disturb": "busy", "dnd": "busy", "occupied": "busy", "engaged": "busy",
                "free": "free", "open": "free", "available": "free", "come in": "free"}


def _store(home, name: str) -> Path:
    return Path(home.config.resolve_path(home.config.data_dir)) / "life" / f"{name}.json"


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


def mins(seconds: float) -> int:
    return max(0, round(seconds / 60))


# ---------------------------------------------------------------- study mode
class Study:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("study") or {}
        self.focus = float(c.get("focus", 25))
        self.short = float(c.get("break", 5))
        self.long = float(c.get("long_break", 15))
        self.every = int(c.get("long_every", 4))
        self.light = c.get("light") or []
        self.light_after = c.get("light_after") or []
        self.sessions: dict[str, dict[str, Any]] = {}
        self.path = _store(home, "study")
        self._lock = threading.Lock()

    def _say(self, key: str, room: str, **v) -> str:
        st = self.sessions.get((room or "").lower()) or {}
        rooms = [r for r in dict.fromkeys([room, *st.get("members", [])]) if r]
        if len(rooms) > 1:  # studying together: everyone in the session hears the breaks
            return self.home.voice.speak(key, rooms=rooms, **v)
        return self.home.voice.speak(key, room=room, **v)

    def join(self, room: str, other_room: str) -> bool:
        """Join the session in `room` from `other_room` (study together)."""
        st = self.sessions.get(room.lower())
        if not st:
            return False
        members = st.setdefault("members", [])
        if other_room and other_room.lower() not in members and other_room.lower() != room.lower():
            members.append(other_room.lower())
        return True

    def start(self, room: str | None, person: str | None = None, minutes: float | None = None) -> str:
        h = self.home
        room = (room or h.default_room() or "").lower()
        inst = h.instances.all.get(person) if person else None
        inst = inst or h.instances.for_room(room)
        focus = float(minutes or self.focus)
        with self._lock:
            had = self.sessions.get(room)
            self.sessions[room] = {"person": inst.id, "phase": "focus", "until": time.time() + focus * 60,
                                   "length": focus, "blocks": 0, "focus_seconds": 0.0, "phase_start": time.time(),
                                   "started": time.time(), "skipped": 0,
                                   "dnd_before": had["dnd_before"] if had else h.instances.dnd(inst)}
        if not inst.main:
            h.instances.set_dnd(inst, True)
        self._steps(self.light, room)
        h.events.log("study.on", room=room, person=inst.id)
        self._say("study_on", room, minutes=_n(focus))
        return f"study mode on in {room}"

    def stop(self, room: str | None) -> str:
        h = self.home
        room = (room or "").lower()
        with self._lock:
            if room not in self.sessions and len(self.sessions) == 1:
                room = next(iter(self.sessions))
            st = self.sessions.pop(room, None)
        if st is None:
            self._say("study_not_on", room or None)
            return "study mode isn't on"
        if st["phase"] == "focus":
            st["focus_seconds"] += time.time() - st["phase_start"]
            if time.time() - st["phase_start"] >= 0.6 * st["length"] * 60:
                st["blocks"] += 1
        inst = h.instances.all.get(st["person"]) or h.instances.for_room(room)
        if not inst.main:
            h.instances.set_dnd(inst, bool(st["dnd_before"]))
        self._steps(self.light_after, room)
        minutes = mins(st["focus_seconds"])
        self._log(st["person"], minutes, st["blocks"])
        h.events.log("study.off", room=room, person=st["person"], minutes=minutes, blocks=st["blocks"])
        summary = (f"You did {st['blocks']} focus block{'s' if st['blocks'] != 1 else ''}, "
                   f"{minutes} minutes in all." if minutes else "")
        self._say("study_off", room, summary=summary)
        return f"study mode off ({minutes} minutes)"

    def skip_break(self, room: str | None) -> str:
        st = self.sessions.get((room or "").lower())
        if not st or st["phase"] != "break":
            return "not on a break"
        st["skipped"] += 1
        self._to_focus(room.lower(), st)
        return "break skipped"

    def left(self, room: str | None) -> str:
        st = self.sessions.get((room or "").lower())
        if not st:
            self._say("study_not_on", room)
            return "not studying"
        left = clock.spoken_duration(max(0, st["until"] - time.time()))
        self._say("study_left", room, left=left, phase="focus block" if st["phase"] == "focus" else "break")
        return left

    def studying(self, room: str | None) -> bool:
        return (room or "").lower() in self.sessions

    def tick(self) -> None:
        now = time.time()
        for room, st in list(self.sessions.items()):
            if now < st["until"]:
                continue
            if st["phase"] == "focus":
                st["focus_seconds"] += now - st["phase_start"]
                st["blocks"] += 1
                if st["skipped"] >= 2:
                    self._say("study_nag", room, minutes=mins(now - st["started"]))
                    st["skipped"] = 0
                long = self.every > 0 and st["blocks"] % self.every == 0
                length = self.long if long else self.short
                st.update(phase="break", until=now + length * 60, phase_start=now)
                if long:
                    self._say("study_long_break", room, blocks=st["blocks"], minutes=_n(length))
                else:
                    self._say("study_break", room, minutes=_n(length))
            else:
                self._to_focus(room, st)

    def _to_focus(self, room: str, st: dict) -> None:
        now = time.time()
        st.update(phase="focus", until=now + st["length"] * 60, phase_start=now)
        self._say("study_back", room, minutes=_n(st["length"]))

    def _steps(self, steps: list, room: str) -> None:
        if steps and room:
            threading.Thread(target=self.home.run_steps, args=(steps, {"here": room}, "study mode"),
                             daemon=True).start()

    def _log(self, person: str, minutes: int, blocks: int) -> None:
        if not minutes:
            return
        with self._lock:
            data = _read(self.path, [])
            data.append({"person": person, "minutes": minutes, "blocks": blocks,
                         "date": clock.now().strftime("%Y-%m-%d")})
            _write(self.path, data[-500:])

    def week(self, days: int = 7) -> dict[str, int]:
        since = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        out: dict[str, int] = {}
        for r in _read(self.path, []):
            if r["date"] >= since:
                out[r["person"]] = out.get(r["person"], 0) + int(r["minutes"])
        return out


# ---------------------------------------------------------------- exams
class Exams:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("exams") or {}
        self.quiet_from = str(c.get("quiet_from", "21:30"))
        self.heads_up_at = str(c.get("heads_up", "19:00"))
        self.good_luck_at = str(c.get("good_luck", "07:30"))
        self.use_calendar = bool(c.get("from_calendar", True))
        self.path = _store(home, "exams")
        self._lock = threading.Lock()
        self._cal: dict[str, list[dict]] = {}
        self._cal_at = 0.0
        self._done: set[str] = set()

    # ---------- what's coming up ----------
    def _config(self, inst) -> list[dict]:
        raw = inst.extra.get("exams") or []
        out = []
        for e in raw if isinstance(raw, list) else [raw]:
            try:
                if isinstance(e, dict):
                    out.append({"subject": str(e.get("subject", "exam")), "at": _parse_at(str(e["at"]))})
            except (KeyError, ValueError):
                log.warning("exam for %s needs `at: \"YYYY-MM-DD HH:MM\"`: %s", inst.name, e)
        return out

    def _calendar(self, inst) -> list[dict]:
        if not (self.use_calendar and inst.calendar):
            return []
        if time.monotonic() - self._cal_at > 1800 or inst.id not in self._cal:
            try:
                from .calendar_feed import upcoming

                evs = upcoming(inst.calendar, days=15)
                self._cal[inst.id] = [{"subject": e["summary"], "at": _parse_at(e["start"] if not e["all_day"]
                                                                                 else e["start"] + " 09:00")}
                                      for e in evs if EXAM_WORDS.search(e.get("summary", ""))]
            except Exception as e:
                log.debug("exams from %s's calendar: %s", inst.name, e)
                self._cal.setdefault(inst.id, [])
            self._cal_at = time.monotonic()
        return self._cal.get(inst.id, [])

    def all(self, days: float = 14) -> list[tuple[Any, dict]]:
        now = clock.now()
        saved = _read(self.path, {})
        out = []
        for inst in self.home.instances.all.values():
            items = self._config(inst) + self._calendar(inst) + [
                {"subject": e["subject"], "at": datetime.fromisoformat(e["at"])} for e in saved.get(inst.id, [])]
            seen = set()
            for e in items:
                at = e["at"] if e["at"].tzinfo else e["at"].astimezone()
                k = (e["subject"].lower(), at.strftime("%Y-%m-%d %H:%M"))
                if k in seen or not (now - timedelta(hours=3) <= at <= now + timedelta(days=days)):
                    continue
                seen.add(k)
                out.append((inst, {"subject": e["subject"], "at": at}))
        return sorted(out, key=lambda x: x[1]["at"])

    def add(self, inst, subject: str, at: datetime) -> dict:
        with self._lock:
            saved = _read(self.path, {})
            mine = [e for e in saved.get(inst.id, []) if datetime.fromisoformat(e["at"]) > clock.now() - timedelta(days=1)]
            mine.append({"subject": subject or "exam", "at": at.isoformat(timespec="minutes")})
            saved[inst.id] = mine
            _write(self.path, saved)
        self.home.events.log("exam.added", person=inst.id, subject=subject, at=at.isoformat(timespec="minutes"))
        return {"subject": subject, "at": at}

    def remove(self, inst, words: str = "") -> int:
        with self._lock:
            saved = _read(self.path, {})
            mine = saved.get(inst.id, [])
            keep = [e for e in mine if words and words.lower() not in e["subject"].lower()]
            if not words:
                keep = sorted(mine, key=lambda e: e["at"])[1:]
            saved[inst.id] = keep
            _write(self.path, saved)
        return len(mine) - len(keep)

    def tomorrow(self, now: datetime | None = None) -> list[tuple[Any, dict]]:
        now = now or clock.now()
        day = (now + timedelta(days=1)).date()
        return [(i, e) for i, e in self.all(3) if e["at"].date() == day]

    def today(self, now: datetime | None = None) -> list[tuple[Any, dict]]:
        now = now or clock.now()
        return [(i, e) for i, e in self.all(2) if e["at"].date() == now.date() and e["at"] > now - timedelta(hours=3)]

    def quiet_time(self, now: datetime | None = None) -> tuple[datetime, list] | None:
        """(when tonight's exam quiet starts, who it's for), if anyone has an exam tomorrow."""
        now = now or clock.now()
        who = self.tomorrow(now)
        if not who:
            return None
        t = clock.parse_hhmm(self.quiet_from)
        return now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0), who

    def quiet_now(self, now: datetime | None = None) -> bool:
        now = now or clock.now()
        q = self.quiet_time(now)
        if q and now >= q[0]:
            return True
        return any(now < e["at"] and now.hour < 9 for _, e in self.today(now))  # the morning of

    def names(self, who: list) -> tuple[str, str]:
        names = sorted({i.name for i, _ in who})
        text = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return text, "has" if len(names) == 1 else "have"

    # ---------- the night before, the morning of ----------
    def tick(self, now: datetime | None = None) -> None:
        now = now or clock.now()
        h = self.home
        q = self.quiet_time(now)
        day = now.strftime("%Y-%m-%d")
        if q:
            quiet_at, who = q
            who_text, has = self.names(who)
            first = min(e["at"] for _, e in who)
            hu = clock.parse_hhmm(self.heads_up_at)
            if (now.hour, now.minute) >= (hu.hour, hu.minute) and f"heads:{day}" not in self._done:
                self._done.add(f"heads:{day}")
                vals = dict(who=who_text, has=has, time=clock.spoken_time(first), quiet=clock.spoken_time(quiet_at))
                text, _ = h.voice.resolve("exam_heads_up", **vals)
                examinees = {i.id for i, _ in who}
                for inst in h.instances.all.values():
                    if inst.id not in examinees and not inst.main:
                        h.notify_person(inst, text, title="Exam tomorrow")
                h.voice.speak("exam_heads_up", rooms=h.instances.main.rooms or None, **vals)
            if now >= quiet_at and f"quiet:{day}" not in self._done:
                self._done.add(f"quiet:{day}")
                h.events.log("exam.quiet", people=",".join(i.id for i, _ in who))
                h.voice.speak("exam_quiet_start", rooms=h.instances.main.rooms or None, who=who_text, has=has)
        gl = clock.parse_hhmm(self.good_luck_at)
        for inst, e in self.today(now):
            key = f"luck:{inst.id}:{day}"
            if key in self._done or now >= e["at"]:
                continue
            if (now.hour, now.minute) >= (gl.hour, gl.minute) or e["at"] - now <= timedelta(hours=2):
                self.good_luck(inst)

    def good_luck(self, inst) -> None:
        key = f"luck:{inst.id}:{clock.now().strftime('%Y-%m-%d')}"
        if key in self._done:
            return
        self._done.add(key)
        h = self.home
        h.voice.speak("exam_good_luck", room=inst.rooms[0] if inst.rooms else None, name=inst.name)
        text, _ = h.voice.resolve("exam_good_luck", name=inst.name)
        h.notify_person(inst, text, title="Exam today")

    def on_event(self, ev: dict) -> None:
        if ev["kind"] == "agenda.alarm":  # their alarm on exam day: good luck with the wake-up
            inst = self.home.instances.all.get(ev.get("instance"))
            if inst and any(i.id == inst.id for i, _ in self.today()):
                self.good_luck(inst)

    def spoken_list(self) -> str:
        out = []
        for inst, e in self.all(14)[:6]:
            out.append(f"{inst.name}, {e['subject']}, {_day(e['at'])} at {clock.spoken_time(e['at'])}")
        return "; ".join(out)

    # ---------- "I've got a calc exam on Friday at 9" ----------
    def heard(self, said: str, speaker) -> tuple[str, dict] | None:
        s = said.lower().replace("’", "'").strip(" .!?")
        people = self.home.people.housemates() if getattr(self.home, "people", None) else []
        names = sorted({n for i in people for n in (i.id, i.name.lower())}, key=len, reverse=True)
        who = "|".join(re.escape(n) for n in names) or r"(?!x)x"
        m = re.match(rf"^(?:(i)(?: have|'ve got| got|'ve) |({who}) (?:has|'s got|has got) )(?:an? |my |their |his |her )?"
                     r"(.*?)\b(?:exam|final|midterm|test)s?\b(.*)$", s)
        if not m:
            if re.match(r"^(?:what|when|which) (?:exams|tests)|^(?:any|who has|who's got) (?:an? )?exams?", s):
                text = self.spoken_list()
                return ("exam_list", {"list": text}) if text else ("exam_none", {})
            return None
        inst = speaker if m.group(1) else self.home.people.find(m.group(2))
        if inst is None or inst.main:
            return None
        subject = re.sub(r"^(?:a|an|the|my) ", "", m.group(3).strip()) or "exam"
        at = parse_when(m.group(4))
        if at is None:
            return None  # "I have an exam soon": let the AI ask when
        self.add(inst, subject, at)
        return "exam_saved", {"subject": subject if subject != "exam" else "the", "when": f"{_day(at)} at {clock.spoken_time(at)}",
                              "quiet": clock.spoken_time(at.replace(hour=clock.parse_hhmm(self.quiet_from).hour,
                                                                     minute=clock.parse_hhmm(self.quiet_from).minute))}


def _parse_at(s: str) -> datetime:
    s = s.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).astimezone()
        except ValueError:
            continue
    return datetime.fromisoformat(s).astimezone()


def _day(at: datetime) -> str:
    today = clock.now().date()
    if at.date() == today:
        return "today"
    if at.date() == today + timedelta(days=1):
        return "tomorrow"
    return at.strftime("%A")


DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """'tomorrow at 9', 'on friday at 2pm', 'next monday', 'today at 14:30' -> a time (9am if none)."""
    now = now or clock.now()
    t = text.lower()
    day = None
    if "tomorrow" in t:
        day = now.date() + timedelta(days=1)
    elif "today" in t or "tonight" in t or "this afternoon" in t or "this morning" in t:
        day = now.date()
    else:
        for i, d in enumerate(DAYS):
            if re.search(rf"\b{d}\b|\b{d[:3]}\b", t):
                ahead = (i - now.weekday()) % 7 or 7  # the coming one ("next friday" too)
                day = now.date() + timedelta(days=ahead)
                break
    if day is None:
        return None
    hour, minute = 9, 0
    m = re.search(r"\b(?:at )?(\d{1,2})(?:[:.](\d{2}))? ?(am|pm|a\.m\.|p\.m\.)?\b", t)
    words = {w: n for n, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
                                         "nine", "ten", "eleven", "twelve"])}
    wm = re.search(r"\bat (" + "|".join(words) + r")(?: (thirty|fifteen|forty five))? ?(am|pm|in the morning|in the afternoon)?\b", t)
    if m and (m.group(3) or ":" in m.group(0) or "at" in m.group(0)):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        elif not ampm and 1 <= hour <= 7:
            hour += 12  # "at 2" is the afternoon
    elif wm:
        hour = words[wm.group(1)]
        minute = {"thirty": 30, "fifteen": 15, "forty five": 45}.get(wm.group(2) or "", 0)
        tail = wm.group(3) or ""
        if (tail in ("pm", "in the afternoon") or (not tail and 1 <= hour <= 7)) and hour < 12:
            hour += 12
    if hour > 23 or minute > 59:
        return None
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=now.tzinfo)


# ---------------------------------------------------------------- door status lights
class DoorStatus:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("door_status") or {}
        self.rooms = {str(r).lower(): dict(v or {}) for r, v in (c.get("rooms") or {}).items()}
        for inst in home.instances.all.values():
            if inst.extra.get("door_light") and inst.rooms:
                self.rooms.setdefault(inst.rooms[0], dict(inst.extra["door_light"]))
        self.colors = {"studying": ["red", "green"], "sleeping": ["blue"], "busy": ["red"], "free": ["green"],
                       **{k: list(v) for k, v in (c.get("colors") or {}).items()}}
        self.manual: dict[str, tuple[str, float]] = {}
        self.shown: dict[str, str] = {}

    def status(self, room: str) -> str:
        h = self.home
        room = room.lower()
        st, until = self.manual.get(room, ("", 0.0))
        if st and (not until or time.time() < until):
            return st
        if room in getattr(h.freak, "active", {}):
            return "busy"  # never says why
        life = getattr(h, "life", None)
        if life and life.study.studying(room):
            return "studying"
        inst = h.instances.for_room(room)
        if not inst.main:
            rooms = getattr(h, "rooms", None)
            in_bed = getattr(rooms, "in_bed", None) or {}
            if inst.id in in_bed or inst.quiet_now():
                return "sleeping"
            if h.instances.dnd(inst):
                return "busy"
        return "free"

    def set(self, room: str | None, status: str, minutes: float | None = None) -> str:
        room = (room or "").lower()
        if not room:
            raise ValueError("which room's door?")
        status = STATUS_WORDS.get(status.lower().strip(), status.lower().strip())
        if status in ("clear", "auto", "none", "off", "normal"):
            self.manual.pop(room, None)
            self.refresh()
            self.home.voice.speak("door_status_clear", room=room)
            return "door status cleared"
        if status not in STATUSES:
            raise ValueError(f"door status is one of {', '.join(STATUSES)}")
        self.manual[room] = (status, time.time() + float(minutes) * 60 if minutes else 0.0)
        self.refresh()
        self.home.voice.speak("door_status_set", room=room, status=_status_words(status))
        return f"{room} door: {status}"

    def all(self) -> dict[str, str]:
        rooms = set(self.rooms) | {r for i in self.home.instances.all.values() if not i.main for r in i.rooms[:1]}
        return {r: self.status(r) for r in sorted(rooms)}

    def refresh(self) -> None:
        for room, cfg in self.rooms.items():
            st = self.status(room)
            if self.shown.get(room) == st:
                continue
            self.shown[room] = st
            try:
                self._show(room, cfg, st)
            except Exception as e:
                log.warning("door light for %s: %s", room, e)

    def _show(self, room: str, cfg: dict, st: str) -> None:
        h = self.home
        if st in cfg:  # your own steps per status (a smart bulb, Home Assistant...)
            h.run_steps(cfg[st] if isinstance(cfg[st], list) else [cfg[st]], {"here": room}, "door status")
            return
        if "node" not in cfg:
            return
        channels = cfg.get("channels") or ["red", "green", "blue"]
        names = {"red": channels[0], "green": channels[1 % len(channels)], "blue": channels[2 % len(channels)]}
        lit = {names.get(c, c) for c in self.colors.get(st, [])}
        backend = cfg.get("backend") or h.config.settings.default_backend
        for ch in dict.fromkeys(names.values()):
            h.run_action({"backend": backend, "node": cfg["node"], "channel": ch, "state": ch in lit})

    def heard(self, said: str, room: str | None, speaker) -> tuple[str, dict] | str | None:
        s = said.lower().replace("’", "'").strip(" .!?")
        words = "|".join(sorted((re.escape(w) for w in [*STATUS_WORDS, "clear", "auto", "normal"]), key=len, reverse=True))
        m = re.match(rf"^(?:set |put |make |change )?(?:my |the )?door(?: status| light| sign)?(?: to| says| on| as)? ({words})"
                     rf"(?: for (\d+) (?:minutes|mins|hours?))?$", s) or \
            re.match(rf"^(?:mark me as|i'm|i am) ({words})(?: for (\d+) (?:minutes|mins|hours?))?$", s) \
            if re.search(r"door|mark me", s) else None
        if m:
            target = room
            if speaker is not None and not speaker.main and speaker.rooms:
                target = speaker.rooms[0]
            mins_ = float(m.group(2)) * (60 if "hour" in s else 1) if m.group(2) else None
            self.set(target, m.group(1), mins_)
            return "done"
        people = [i for i in self.home.instances.all.values() if not i.main]
        names = sorted({n for i in people for n in (i.id, i.name.lower())}, key=len, reverse=True)
        who = "|".join(re.escape(n) for n in names) or r"(?!x)x"
        m = re.match(rf"^(?:is ({who}) (?:busy|free|studying|asleep|sleeping|available)|can i (?:go in(?:to)?|knock on) ({who})'?s? "
                     rf"(?:room|door)|what(?:'s| does) ({who})'?s? door (?:say|status))$", s)
        if m:
            inst = self.home.people.find(next(g for g in m.groups() if g))
            room = inst.rooms[0] if inst.rooms else ""
            return "door_status_is", {"whose": f"{inst.name}'s door", "status": _status_words(self.status(room))}
        return None


def _status_words(st: str) -> str:
    return {"busy": "busy, do not disturb", "free": "free, come on in", "sleeping": "sleeping",
            "studying": "studying"}.get(st, st)


def _n(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"
