"""Personal helpers, and a few house-wide moments.

What did I miss  "what did I miss?": what happened since you left (deliveries, visitors, polls, jobs you were
                 given, points, calendar changes). With missed: {auto: true} it tells you as you get home.
My calendar      "add dentist on Tuesday at 3 to my calendar": into your real calendar over CalDAV (iCloud,
                 Fastmail, Nextcloud: instances.<you>.calendar_write), else a calendar the house keeps for you
                 at /cal/<you>.ics, with a reminder an hour before either way.
Where I put it   "my passport's in the desk drawer", "where's my passport?". Keys, cards and passports are only
                 told in your own room, or to your own voice.
Reminders by     "remind me when I get home to call mum", "remind me when I leave to take my laptop",
place            "remind Parker when they get home to pay rent".
Nicknames        "Parks means Parker", "call me Finny".
My usual         "set my usual to lamp on and play lo-fi" (parts are your existing commands), then "my usual".
TV cut-off       "turn the TV off at midnight", "cut me off at 1am", "TV sleep timer 45 minutes": a warning 10
                 minutes before, then off. "cut me off" can't be cancelled (tv: {strict: true}). Uses
                 tv: {off: [steps]} per room, a tv_off command, or a plug called tv.
Mood dial        mood_dial: {node, input, scenes: [...]}: a knob on the wall picks the scene.
Cleaning blitz   "cleaning blitz": 20 minutes, a playlist, a job for everyone home; finishing yours is +1.
Semester break   "semester break mode": vacation mode, alarms paused, `on` steps (eco heating); back to
                 normal when the first person's home again (or "semester break off").
Horoscopes       Monday morning, or "read the horoscopes": a roast horoscope for each housemate from their
                 week. Satire, not astrology.
Year in review   "year in review": the house's year in numbers, as a show.

tv: { "living room": { off: [ { backend: google, say: turn off the TV } ] }, strict: true }
mood_dial: { node: lounge, input: dial, scenes: [ { upto: 25, name: chill, do: [...] }, { upto: 60, name: study, do: [...] } ] }
blitz: { minutes: 20, playlist: "cleaning bangers", jobs: [kitchen benches, vacuum the lounge, bathroom sink] }
semester_break: { on: [ { node: hall, channel: heating, level: 15 } ], off: [ { node: hall, channel: heating, level: 20 } ] }
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta

from . import clock
from .life_base import Part, clean, join

PRIVATE_THINGS = re.compile(r"\b(keys?|passport|card|cards|wallet|cash|money|password|pin|licen[cs]e|id)\b")
BLITZ_JOBS = ["kitchen benches and dishes", "vacuum the lounge", "bathroom sink and mirror", "rubbish and recycling",
              "tidy the lounge and couches", "wipe the table and stovetop"]


def _when(at: datetime) -> str:
    today = clock.now().date()
    day = "today" if at.date() == today else "tomorrow" if at.date() == today + timedelta(days=1) else at.strftime("%A")
    return f"{day} at {clock.spoken_time(at)}"


# ================================================================ what did I miss
class Missed(Part):
    section = "missed"
    KINDS = {"package.delivered": "a parcel arrived", "doorbell": "the doorbell rang", "scan": "someone came to the door",
             "intercom": "an announcement", "poll.result": "a poll result", "calendar.added": "a calendar addition",
             "meeting.booked": "a house meeting got booked", "party.on": "a party started", "walk.overdue": "someone was late home",
             "locked_out": "someone got locked out", "smoke": "a smoke alarm", "last_out": "a door was left unlocked"}

    def since_left(self, inst) -> str:
        ev = [e for e in self.home.events.query("presence.left", limit=500) if e.get("person") == inst.id]
        return ev[-1]["time"] if ev else (clock.now() - timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")

    def summary(self, inst) -> str:
        since = self.since_left(inst)
        evs = [e for e in self.home.events.query(since=since, limit=500)]
        bits: list[str] = []
        c = Counter(e["kind"] for e in evs if e["kind"] in self.KINDS)
        for k, n in c.most_common():
            bits.append(self.KINDS[k] + (f" ({n} times)" if n > 1 else ""))
        jobs = [e for e in evs if e["kind"] == "job.given" and e.get("person") == inst.id]
        if jobs:
            bits.append("you were given a job")
        pts = sum(int(e.get("delta", 0)) for e in evs if e["kind"] == "points" and e.get("person") == inst.id)
        if pts:
            bits.append(f"your points went {'up' if pts > 0 else 'down'} by {abs(pts)}")
        came = sorted({self.name(e.get("person")) for e in evs if e["kind"] == "presence.arrived" and e.get("person") != inst.id})
        if came:
            bits.append(f"{join(came)} came home")
        for e in evs:
            if e["kind"] == "poll.result":
                bits.append(f"the poll on {e.get('question', '').rstrip('?')} went {e.get('result', '')}")
                break
        if not bits:
            return self.text("missed_nothing", name=inst.name)
        return self.text("missed_summary", name=inst.name, items=join(bits[:7]))

    def on_event(self, ev):
        if ev["kind"] == "presence.arrived" and self.cfg.get("auto"):
            inst = self.home.instances.all.get(ev.get("person"))
            if inst and inst.rooms:
                self.home.timeline.call_in(90, lambda: self.home.voice.speak(self.summary(inst), room=inst.rooms[0]))

    def heard(self, said, room, speaker):
        if re.match(r"^what did i miss|^what's happened (?:since i left|while i was out)|^anything happen while i was out", clean(said)):
            if speaker is None or speaker.main:
                return None
            self.say(self.summary(speaker), room=room)
            return "done"
        return None


# ================================================================ my calendar
class MyCalendar(Part):
    store = "my_calendar"

    def add(self, inst, what: str, at: datetime, minutes: float = 60) -> str:
        cfg = inst.extra.get("calendar_write") or {}
        uid = uuid.uuid4().hex
        ics = _vevent(uid, what, at, at + timedelta(minutes=minutes))
        where = "the house's calendar for you"
        if cfg.get("url"):
            import os

            import requests

            url = str(cfg["url"]).rstrip("/") + f"/{uid}.ics"
            pw = os.environ.get(str(cfg.get("password_env", "")), "") or str(cfg.get("password", ""))
            r = requests.put(url, data=ics.encode(), auth=(str(cfg.get("username", "")), pw),
                             headers={"Content-Type": "text/calendar; charset=utf-8"}, timeout=15)
            r.raise_for_status()
            where = "your calendar"
        d = self.load({})
        d.setdefault(inst.id, []).append({"uid": uid, "what": what, "at": at.isoformat(timespec="minutes"),
                                          "end": (at + timedelta(minutes=minutes)).isoformat(timespec="minutes")})
        self.save(d)
        if at - timedelta(hours=1) > clock.now():
            self.home.instances.add(inst, "reminder", at - timedelta(hours=1), text=f"{what} in an hour")
        self.home.events.log("my_calendar.added", person=inst.id, what=what)
        return where

    def ics(self, pid: str) -> str:
        rows = self.load({}).get(pid, [])
        body = "".join(_vevent(r["uid"], r["what"], datetime.fromisoformat(r["at"]), datetime.fromisoformat(r["end"]),
                               wrap=False) for r in rows)
        return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//phrasehome//me//EN\r\n{body}END:VCALENDAR\r\n"

    def heard(self, said, room, speaker):
        s = clean(said)
        m = re.match(r"^(?:add|put) (.+?) (?:to|on|in|into) my calendar(?: (.+))?$", s) or \
            re.match(r"^(?:add|put) to my calendar[:,]? (.+)$", s)
        if not m or speaker is None or speaker.main:
            return None
        from .study import parse_when

        text = (m.group(1) + " " + (m.group(2) or "")) if m.lastindex and m.lastindex >= 2 else m.group(1)
        at = parse_when(" " + text)
        if at is None:
            return "calendar_when", {}
        what = re.sub(r"\s+(?:on|at|this|next|tomorrow|today|tonight)\b.*$", "", m.group(1)).strip() or m.group(1)
        try:
            where = self.add(speaker, what, at)
        except Exception as e:
            return "calendar_failed", {"why": str(e)[:80]}
        return "calendar_added_mine", {"what": what, "when": _when(at), "where": where}


def _vevent(uid: str, what: str, start: datetime, end: datetime, wrap: bool = True) -> str:
    f = "%Y%m%dT%H%M%SZ"
    from datetime import timezone

    ev = (f"BEGIN:VEVENT\r\nUID:{uid}@phrasehome\r\nDTSTAMP:{datetime.now(timezone.utc).strftime(f)}\r\n"
          f"DTSTART:{start.astimezone(timezone.utc).strftime(f)}\r\nDTEND:{end.astimezone(timezone.utc).strftime(f)}\r\n"
          f"SUMMARY:{what}\r\nEND:VEVENT\r\n")
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//phrasehome//me//EN\r\n{ev}END:VCALENDAR\r\n" if wrap else ev


# ================================================================ where I put things
class Things(Part):
    store = "things"

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        d = self.load({"things": []})
        m = re.match(r"^(?:i put |i've put |i left |i've left )?my (.+?)(?:'s| is| are|'re) (?:in|on|under|behind|inside|next to|at|by) (.+)$", s) or \
            re.match(r"^i (?:put|left|moved) my (.+?) (?:in|on|under|behind|inside|next to|at|by|to) (.+)$", s)
        prep = re.search(r" (in|on|under|behind|inside|next to|at|by|to) ", s)
        if m and me is not None:
            item, where = m.group(1), m.group(2)
            where = f"{prep.group(1) if prep and prep.group(1) != 'to' else 'in'} {where}"
            d["things"] = [t for t in d["things"] if not (t["owner"] == me.id and t["item"] == item)]
            d["things"].append({"item": item, "where": where, "owner": me.id, "time": clock.now().strftime("%Y-%m-%d"),
                                "private": bool(PRIVATE_THINGS.search(item))})
            self.save(d)
            return "things_saved", {"item": item, "where": where}
        m = re.match(r"^the (.+?)(?:'s| is| are) (in|on|under|behind|inside|next to|at|by) (.+)$", s)
        if m and not re.search(r"\b(weather|time|bathroom|tv)\b", m.group(1)):
            d["things"] = [t for t in d["things"] if not (t["owner"] == "house" and t["item"] == m.group(1))]
            d["things"].append({"item": m.group(1), "where": f"{m.group(2)} {m.group(3)}", "owner": "house",
                                "time": clock.now().strftime("%Y-%m-%d"), "private": bool(PRIVATE_THINGS.search(m.group(1)))})
            self.save(d)
            return "things_saved", {"item": m.group(1), "where": f"{m.group(2)} {m.group(3)}"}
        m = re.match(rf"^(?:where's|where is|where are|where did i put|have you seen) (my|the|({self.who_rx()})'s) (.+)$", s) or \
            re.match(rf"^where did ({self.who_rx()}) put (?:the|their|his|her) (.+)$", s)
        if m:
            if m.lastindex == 2 and not m.group(1) in ("my", "the"):  # "where did finn put the remote"
                owner, item = self.find(m.group(1)), m.group(2)
            else:
                item = m.group(3)
                owner = me if m.group(1) == "my" else (self.find(m.group(2)) if m.group(2) else None)
            words = set(item.split()) - {"the", "my", "a"}
            hits = [t for t in reversed(d["things"]) if words & set(t["item"].split())
                    and (t["owner"] == "house" or owner is None or t["owner"] == getattr(owner, "id", None))]
            if not hits:
                return None
            t = hits[0]
            if t["private"] and not self._private_ok(t, room, me):
                return "things_private", {}
            return "things_where", {"item": t["item"], "where": t["where"], "since": _since(t["time"])}
        return None

    def _private_ok(self, t: dict, room, me) -> bool:
        if me is not None and t["owner"] == me.id:
            return True  # their own voice, or their own room
        doors = {str(sc.get("room", "")).lower() for sc in self.home.config.door_scanners}
        owner = self.home.instances.all.get(t["owner"])
        return bool(owner) and (room or "").lower() in owner.rooms and (room or "").lower() not in doors


def _since(d: str) -> str:
    day = datetime.strptime(d, "%Y-%m-%d").date()
    n = (clock.now().date() - day).days
    return "today" if n == 0 else "yesterday" if n == 1 else f"on {day.strftime('%A')}" if n < 7 else f"{n} days ago"


# ================================================================ reminders by place
class PlaceReminders(Part):
    store = "place_reminders"

    def heard(self, said, room, speaker):
        s = clean(said)
        who = self.who_rx()
        m = re.match(rf"^remind (me|({who})) when (?:i|they|he|she|({who})) (?:get|gets|come|comes|arrive|arrives) (?:home|back)(?: to)? (.+)$", s) or \
            re.match(rf"^remind (me|({who})) when (?:i|they|he|she) (leave|leaves|go out|goes out|head out)(?: to)? (.+)$", s) or \
            re.match(rf"^when (i|({who})) (?:get|gets) home,? remind (?:me|them|him|her) (?:to )?(.+)$", s)
        if not m:
            return None
        target = speaker if m.group(1) in ("me", "i") else self.find(m.group(2))
        if target is None or target.main:
            return None
        leave = bool(re.search(r"\bwhen (?:i|they|he|she) (?:leave|leaves|go out|goes out|head out)\b", s))
        text = m.groups()[-1]
        d = self.load({"items": []})
        d["items"].append({"id": uuid.uuid4().hex[:6], "person": target.id, "on": "leave" if leave else "arrive",
                           "text": text, "by": getattr(speaker, "id", "")})
        self.save(d)
        return "place_saved", {"name": "you" if target is speaker else target.name, "on": "leave" if leave else "get home",
                               "text": text}

    def on_event(self, ev):
        kind = {"presence.arrived": "arrive", "presence.left": "leave"}.get(ev["kind"])
        if not kind:
            return
        d = self.load({"items": []})
        due = [x for x in d["items"] if x["person"] == ev.get("person") and x["on"] == kind]
        if not due:
            return
        d["items"] = [x for x in d["items"] if x not in due]
        self.save(d)
        inst = self.home.instances.all.get(ev.get("person"))
        for x in due:
            by = "" if x["by"] in ("", inst.id) else f" ({self.name(x['by'])} asked me to tell you)"
            msg = self.text("place_reminder", name=inst.name, text=x["text"], by=by)
            self.home.notify_person(inst, msg, title="Reminder")
            if kind == "arrive" and inst.rooms:
                self.home.timeline.call_in(60, lambda m=msg: self.home.voice.speak(m, room=inst.rooms[0]))


# ================================================================ nicknames
class Nicknames(Part):
    store = "nicknames"

    def __init__(self, home) -> None:
        super().__init__(home)
        for pid, names in self.load({}).items():
            inst = home.instances.all.get(pid)
            if inst:
                inst.people.extend(n for n in names if n not in inst.people)

    def add(self, inst, nick: str) -> None:
        nick = nick.strip().lower()
        d = self.load({})
        d.setdefault(inst.id, [])
        if nick not in d[inst.id]:
            d[inst.id].append(nick)
            self.save(d)
        if nick not in [p.lower() for p in inst.people]:
            inst.people.append(nick)

    def heard(self, said, room, speaker):
        s = clean(said)
        who = self.who_rx()
        m = re.match(rf"^(\w+) means ({who})$|^({who})'s nickname is (\w+)$|^(?:you can )?call ({who}) (\w+)(?: too| as well)?$", s)
        if m:
            g = m.groups()
            inst, nick = (self.find(g[1]), g[0]) if g[0] else (self.find(g[2]), g[3]) if g[2] else (self.find(g[4]), g[5])
            if inst is None or nick in ("me", "him", "her", "them"):
                return None
            self.add(inst, nick)
            return "nickname_saved", {"name": inst.name, "nick": nick}
        m = re.match(r"^(?:you can )?call me (\w+)(?: too| as well| from now on)?$|^my nickname is (\w+)$", s)
        if m and speaker is not None and not speaker.main:
            self.add(speaker, m.group(1) or m.group(2))
            return "nickname_saved", {"name": speaker.name, "nick": m.group(1) or m.group(2)}
        return None


# ================================================================ my usual
class Usual(Part):
    store = "usual"

    def _matcher(self):
        from .matcher import Matcher
        from .understanding import candidates_with_aliases

        s = self.home.config.settings
        return Matcher(candidates_with_aliases(self.home.config), s.match_mode, s.match_threshold, s.block_words,
                               phonetic=bool(getattr(s, 'phonetic_match', True)))

    def run(self, inst, room: str) -> str:
        steps = inst.extra.get("usual") or []
        saved = self.load({}).get(inst.id) or []
        if steps:
            self.home.run_steps(steps, {"here": room}, "my usual")
            return "done"
        if not saved:
            return ""
        cmds = {c.name: c for c in self.home.config.commands}
        for x in saved:
            c = cmds.get(x["command"])
            if c:
                self.home.run_action(c.action, {**x["slots"], "here": room}, "my usual")
        return "done"

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        m = re.match(r"^(?:set|make|save) my usual (?:to|as) (.+)$|^my usual is (.+)$", s)
        if m and me is not None:
            parts = [p.strip() for p in re.split(r",| and then | and | then ", m.group(1) or m.group(2)) if p.strip()]
            matcher = self._matcher()
            found, missed = [], []
            for p in parts:
                hit = matcher.match(p)
                if hit and hit.command.action.get("life") != "usual":
                    found.append({"command": hit.command.name, "slots": hit.slots, "said": p})
                else:
                    missed.append(p)
            if not found:
                return "usual_none", {"missed": join(missed)}
            d = self.load({})
            d[me.id] = found
            self.save(d)
            return "usual_saved", {"items": join(x["said"] for x in found),
                                   "missed": f" I didn't know {join(missed)}." if missed else ""}
        if re.match(r"^(?:my usual|the usual|usual please|do my usual|my usual please)$", s) and me is not None:
            if self.run(me, room or self.room_of(me) or ""):
                return "usual_done", {"name": me.name}
            return "usual_unset", {}
        return None


# ================================================================ TV cut-off
class TVTimer(Part):
    section = "tv"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.timers: dict[str, dict] = {}

    def off(self, room: str) -> None:
        h = self.home
        rc = self.cfg.get(room) or {}
        steps = (rc.get("off") if isinstance(rc, dict) else None) or self.cfg.get("off")
        if steps:
            h.run_steps(steps if isinstance(steps, list) else [steps], {"here": room}, "tv cut-off")
            return
        cmd = next((c for c in h.config.commands if re.search(r"tv.*off|off.*tv|television.*off", c.name)), None)
        if cmd:
            h.run_action(cmd.action, {"here": room}, "tv cut-off")
            return
        energy = getattr(h, "energy", None)
        if energy and "tv" in getattr(energy, "plugs", {}):
            energy.plug("tv").switch(False)
            return
        raise RuntimeError("no way to turn the TV off: add tv: {off: [...]} or a tv_off command")

    def set(self, room: str, at: datetime, strict: bool = False) -> str:
        self.timers[room] = {"at": at.timestamp(), "warned": False, "strict": strict}
        self.home.events.log("tv.timer", room=room, at=at.strftime("%H:%M"))
        return clock.spoken_time(at)

    def tick(self, now):
        for room, t in list(self.timers.items()):
            left = (t["at"] - time.time()) / 60
            if left <= 10 and not t["warned"] and left > 0:
                t["warned"] = True
                self.say("tv_warn", room=room, minutes=max(1, round(left)))
            if left <= 0:
                self.timers.pop(room, None)
                try:
                    self.off(room)
                    self.say("tv_off_now", room=room)
                except Exception as e:
                    self.home.events.log("tv.timer_failed", room=room, why=str(e)[:100])

    def heard(self, said, room, speaker):
        s = clean(said)
        room = (room or "").lower()
        strict = bool(self.cfg.get("strict", True))
        from .study import parse_when

        m = re.match(r"^(?:turn|switch) (?:the )?(?:tv|telly|television) off (?:at|by) (.+)$|^(?:tv|telly) off at (.+)$|"
                     r"^cut me off at (.+)$|^no (?:more )?(?:tv|telly) after (.+)$", s)
        if m:
            t = next(g for g in m.groups() if g)
            t = t.replace("midnight", "12 am").replace("noon", "12 pm")
            at = parse_when(" today at " + t, mode="night") or parse_when(" today " + t, mode="night")
            if at is None:
                return None
            if at <= clock.now():
                at += timedelta(days=1)
            self_imposed = s.startswith("cut me off") or s.startswith("no ")
            return "tv_timer_set", {"time": self.set(room, at, strict and self_imposed)}
        m = re.match(r"^(?:tv|telly|television) (?:sleep timer|off in) (?:for )?(\d+|an?|half an) ?(hours?|minutes?|mins?)$|"
                     r"^(?:turn|switch) (?:the )?(?:tv|telly) off in (\d+|an?|half an) ?(hours?|minutes?|mins?)$|^sleep timer (\d+) ?(minutes?|mins?|hours?)$", s)
        if m:
            g = [x for x in m.groups() if x]
            n = {"a": 1, "an": 1, "half an": 0.5}.get(g[0], None) or float(g[0])
            mins = n * (60 if g[1].startswith("hour") else 1)
            at = clock.now() + timedelta(minutes=mins)
            return "tv_timer_set", {"time": self.set(room, at)}
        if re.match(r"^(?:cancel|stop) the (?:tv|telly) (?:timer|cut ?off)|^(?:give me|one more) (?:another )?(?:episode|half hour)$|^extend the (?:tv|telly)", s):
            t = self.timers.get(room)
            if not t:
                return "tv_no_timer", {}
            if t["strict"]:
                return "tv_strict", {}
            self.timers.pop(room, None)
            return "tv_cancelled", {}
        return None


# ================================================================ mood dial
class MoodDial(Part):
    section = "mood_dial"

    def __init__(self, home) -> None:
        super().__init__(home)
        self._gen = 0
        self.current = ""

    def scene_for(self, value: float) -> dict | None:
        for sc in sorted(self.cfg.get("scenes") or [], key=lambda x: float(x.get("upto", 100))):
            if value <= float(sc.get("upto", 100)):
                return sc
        return None

    def on_event(self, ev):
        c = self.cfg
        if not c or ev["kind"] != "sensor" or ev.get("value") is None:
            return
        if ev.get("node") != str(c.get("node", "")).lower() or ev.get("input") != str(c.get("input", "")).lower():
            return
        self._gen += 1
        gen, value = self._gen, float(ev["value"])
        self.home.timeline.call_in(float(c.get("settle_seconds", 1.5)), lambda: self._apply(gen, value))

    def _apply(self, gen: int, value: float) -> None:
        if gen != self._gen:
            return  # still turning
        sc = self.scene_for(value)
        if not sc or sc.get("name") == self.current:
            return
        self.current = str(sc.get("name", ""))
        room = str(self.cfg.get("room") or (self.shared_rooms() or [""])[0])
        self.home.events.log("mood_dial", scene=self.current, value=value)
        threading.Thread(target=self.home.run_steps, args=(sc.get("do") or [], {"here": room}, "mood dial"), daemon=True).start()
        if self.cfg.get("say", True):
            self.home.voice.speak("mood_dial", room=room, scene=self.current, emotion="soft")


# ================================================================ cleaning blitz
class Blitz(Part):
    section = "blitz"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.active: dict | None = None

    def start(self, room: str | None = None) -> str:
        h = self.home
        here = [self.find(p) for p in h.state.home_people()]
        people = [i for i in here if i and not i.main] or self.housemates()
        jobs = [str(j) for j in (self.cfg.get("jobs") or BLITZ_JOBS)]
        mins = float(self.cfg.get("minutes", 20))
        assigned = {i.id: jobs[n % len(jobs)] for n, i in enumerate(people)}
        self.active = {"until": time.time() + mins * 60, "jobs": assigned, "done": [], "warned": [], "over": False}
        if self.cfg.get("playlist") and getattr(h, "spotify", None):
            try:
                h.spotify.play(playlist=str(self.cfg["playlist"]))
            except Exception:
                pass
        h.events.log("blitz.on", people=",".join(assigned))
        h.voice.speak("blitz_on", rooms=self.shared_rooms(), minutes=int(mins),
                      jobs=join(f"{self.name(p)}, {j}" for p, j in assigned.items()))
        for pid, job in assigned.items():
            self.tell([h.instances.all[pid]], "blitz_job", title="Cleaning blitz", job=job, minutes=int(mins))
        return "blitz on"

    def done(self, inst) -> bool:
        a = self.active
        if not a or inst is None or inst.id not in a["jobs"] or inst.id in a["done"]:
            return False
        a["done"].append(inst.id)
        self.home.life.points.add(inst.id, 1, f"cleaning blitz: {a['jobs'][inst.id]}", inst.id, willing=True)
        return True

    def tick(self, now):
        a = self.active
        if not a:
            return
        left = (a["until"] - time.time()) / 60
        for w in (10, 5, 1):
            if left <= w and w not in a["warned"] and left > 0:
                a["warned"].append(w)
                if left > w - 1:
                    self.home.voice.speak("blitz_left", rooms=self.shared_rooms(), minutes=w)
        if left <= 0 and not a["over"]:
            a["over"] = True
            self.home.voice.speak("blitz_over", rooms=self.shared_rooms())
        if left <= -30:
            self.active = None

    def heard(self, said, room, speaker):
        s = clean(said)
        if re.match(r"^(?:cleaning blitz|blitz the house|let's clean(?: the house)?|clean the house blitz|start a cleaning blitz)$", s):
            self.start(room)
            return "done"
        if self.active and re.match(r"^(?:done|i'm done|finished|i finished(?: my (?:bit|job))?|my bit's done|done my bit)$", s):
            if self.done(speaker):
                return "blitz_done_me", {"name": speaker.name}
        return None


# ================================================================ semester break
class SemesterBreak(Part):
    section = "semester_break"

    def on(self) -> str:
        h = self.home
        d = self.load({})
        if d.get("on"):
            return "already on"
        paused = []
        for inst in h.instances.all.values():
            for a in h.instances.items(inst, ("alarm",)):
                if not a.get("off"):
                    h.instances.update_item(inst, a["id"], off=True)
                    paused.append([inst.id, a["id"]])
        self.save({"on": True, "paused": paused, "mode": h.state.mode, "since": clock.now().strftime("%Y-%m-%d")})
        h.state.set_mode("vacation", reason="semester break")
        if self.cfg.get("on"):
            h.run_steps(self.cfg["on"], {}, "semester break")
        h.events.log("semester_break.on", alarms=len(paused))
        return f"{len(paused)} alarms paused"

    def off(self) -> str:
        h = self.home
        d = self.load({})
        if not d.get("on"):
            return "not on"
        for pid, aid in d.get("paused", []):
            inst = h.instances.all.get(pid)
            try:
                if inst:
                    h.instances.update_item(inst, aid, off=False)
            except KeyError:
                pass
        h.state.set_mode(d.get("mode") or "home", reason="semester break over")
        if self.cfg.get("off"):
            h.run_steps(self.cfg["off"], {}, "semester break over")
        self.save({})
        h.events.log("semester_break.off")
        h.voice.speak("semester_off", rooms=self.shared_rooms())
        return "back to normal"

    def on_event(self, ev):
        if ev["kind"] == "presence.first_home" and self.load({}).get("on"):
            self.off()

    def heard(self, said, room, speaker):
        s = clean(said)
        if re.match(r"^(?:semester break|holiday|holidays|uni break|summer break) mode(?: on)?$|^we're all (?:going|heading) home for the (?:holidays|break)", s):
            return "semester_on", {"result": self.on()}
        if re.match(r"^(?:semester break|holiday) mode off$|^we're (?:all )?back(?: for the semester)?$", s):
            self.off()
            return "done"
        return None


# ================================================================ horoscopes + year in review
class Horoscope(Part):
    def week_stats(self, inst) -> dict:
        life = self.home.life
        from .quiz import week_events

        evs = week_events(self.home, 7)
        late = sum(1 for e in evs if e["kind"] == "presence.arrived" and e.get("person") == inst.id
                   and int(e["time"][11:13]) < 5)
        return {"points": life.points.totals(7).get(inst.id, 0),
                "showers": [r["minutes"] for r in life.watch._rows("shower", 7) if r.get("person") == inst.id],
                "left_unlocked": life.watch.last_out_counts(7).get(inst.id, 0),
                "study_minutes": life.study.week(7).get(inst.id, 0), "late_nights": late,
                "told_house_to_shut_up": sum(1 for e in evs if e["kind"] == "command" and e.get("name") == "hush"
                                             and e.get("person") == inst.id)}

    def read(self, room: str | None = None, only=None) -> str:
        people = [only] if only else self.housemates()
        stats = {i.name: self.week_stats(i) for i in people}
        text = ""
        if getattr(self.home, "assistant", None) is not None:
            try:
                from . import ai

                text = str(ai.ask(self.home, "Write a satirical weekly 'horoscope' for each housemate, one or two short "
                                  "spoken sentences each, roasting their actual week (stats below) like a cheeky house "
                                  "would. Invent a silly star sign for each (like 'the sign of the unwashed mug'). "
                                  f"Plain speech, no lists.\n{stats}", quick=True, max_tokens=700)).strip()
            except Exception:
                text = ""
        if not text:
            bits = []
            for name, st in stats.items():
                if st["left_unlocked"]:
                    bits.append(f"{name}: the stars see an unlocked door in your past. And your future.")
                elif st["showers"] and max(st["showers"]) >= 25:
                    bits.append(f"{name}: water is your element. All of it. Everyone else's too.")
                elif st["study_minutes"] >= 180:
                    bits.append(f"{name}: Saturn rewards your {st['study_minutes'] // 60} hours of study. Nobody else does.")
                elif st["points"] < 0:
                    bits.append(f"{name}: a week of minus points. Mercury isn't in retrograde. You just didn't do the bins.")
                else:
                    bits.append(f"{name}: a quiet week. The stars are bored of you.")
            text = "[cheeky] This week's house horoscopes. " + " ".join(bits)
        self.home.voice.speak(text, rooms=[room] if room else self.shared_rooms())
        return text

    def tick(self, now):
        key = now.strftime("%Y-%W")
        if now.weekday() == 0 and now.hour == 9 and getattr(self, "_done", "") != key:
            self._done = key
            if self.home.state.home_people():
                self.read()

    def heard(self, said, room, speaker):
        s = clean(said)
        if re.match(r"^(?:read )?(?:the |our )?(?:house )?horoscopes?$|^what are the horoscopes", s):
            self.read(room)
            return "done"
        if re.match(r"^(?:read )?my horoscope$|^what's my horoscope", s) and speaker is not None and not speaker.main:
            self.read(room, only=speaker)
            return "done"
        return None


class YearReview(Part):
    def stats(self, days: int = 365) -> dict:
        from .quiz import week_events

        evs = week_events(self.home, days)
        life = self.home.life
        cmds = Counter(e.get("name") for e in evs if e["kind"] == "command" and e.get("name"))
        rows = life.watch._rows("shower", days)
        longest = max(rows, key=lambda r: r["minutes"]) if rows else None
        lo = Counter(r["person"] for r in life.watch._rows("last_out", days))
        study = life.study.week(days)
        pts = life.points.totals(days)
        return {
            "commands": sum(cmds.values()), "top_command": cmds.most_common(1)[0] if cmds else None,
            "hush": cmds.get("hush", 0), "parties": sum(1 for e in evs if e["kind"] == "party.on"),
            "longest_shower": (self.name(longest.get("person")), longest["minutes"]) if longest else None,
            "shower_minutes": sum(r["minutes"] for r in rows),
            "door_left_unlocked": lo.most_common(1)[0] if lo else None,
            "study_hours": {self.name(p): round(m / 60) for p, m in study.items()},
            "points_winner": max(pts.items(), key=lambda x: x[1]) if pts else None,
            "questions": sum(1 for e in evs if e["kind"] == "assistant"),
            "meetings": sum(1 for e in evs if e["kind"] == "meeting.started"),
            "walk_homes": sum(1 for e in evs if e["kind"] == "walk.started"),
        }

    def show(self, room: str | None = None) -> str:
        st = self.stats()
        lines = [f"[cheerful] The house's year in review. You lot gave me {st['commands']} commands"
                 + (f", mostly {st['top_command'][0].replace('_', ' ')}" if st["top_command"] else "") + "."]
        if st["hush"]:
            lines.append(f"[annoyed] You told me to shut up {st['hush']} times. I counted every one.")
        if st["parties"]:
            lines.append(f"[cheerful] {st['parties']} parties.")
        if st["longest_shower"]:
            lines.append(f"[cheeky] Longest shower of the year: {st['longest_shower'][0]}, {st['longest_shower'][1]} minutes. "
                         f"All of you together: {round(st['shower_minutes'] / 60)} hours under the water.")
        if st["door_left_unlocked"]:
            lines.append(f"[annoyed] Door left unlocked champion: {self.name(st['door_left_unlocked'][0])}, "
                         f"{st['door_left_unlocked'][1]} times.")
        if st["study_hours"]:
            top = max(st["study_hours"].items(), key=lambda x: x[1])
            lines.append(f"[calm] Most study mode: {top[0]}, {top[1]} hours.")
        if st["points_winner"]:
            lines.append(f"[cheerful] House points champion: {self.name(st['points_winner'][0])}, with {st['points_winner'][1]}.")
        lines.append("[cheeky] Same again next year. Maybe lock the door this time.")
        text = " ".join(lines)
        self.home.voice.speak(text, rooms=[room] if room else self.shared_rooms())
        self.tell(self.housemates(), "year_review", title="The house's year", text=re.sub(r"\[\w+\] ", "", text))
        return text

    def heard(self, said, room, speaker):
        if re.match(r"^(?:the )?(?:house )?year in review$|^(?:house )?wrapped$|^how was our year", clean(said)):
            self.show(room)
            return "done"
        return None
