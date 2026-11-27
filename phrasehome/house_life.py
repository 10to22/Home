"""House life: the student-house features in one place (home.life).

    people.py     what the house knows about each housemate ("Parker's a vegetarian")
    party.py      party planner: playlist, lights, guest Wi-Fi, quiet-time countdown, cleanup jobs
    study.py      study mode per room, exam-aware quiet hours, door status lights
    meeting.py    house meetings at a time that works for everyone, with an agenda
    housewatch.py shower timer, last one out, windows while away, winter lamp, pollen advice
    walkhome.py   walk-home mode: if they don't turn up, the others hear about it
    quiz.py       the weekly house quiz from the week's events
    radio.py      House Radio: the house as a radio host between songs

Commands for all of them are built in (config.py BUILTIN_COMMANDS: {life: ...}); sentences that
carry details ("add the bins to the meeting agenda", "Parker's walking home from the library",
"I've got a calc exam on Friday at 9") are understood before the AI is asked. Page: /house.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Any

log = logging.getLogger("phrasehome.life")


class HouseLife:
    def __init__(self, home) -> None:
        from .housewatch import Watch
        from .meeting import Meeting
        from .party import Party
        from .people import people_for
        from .quiz import Quiz
        from .radio import Radio
        from .study import DoorStatus, Exams, Study
        from .walkhome import WalkHome

        self.home = home
        home.life = self
        self.people = people_for(home)
        self.study = Study(home)
        self.exams = Exams(home)
        self.door_status = DoorStatus(home)
        self.party = Party(home)
        self.meeting = Meeting(home)
        self.watch = Watch(home)
        self.walk = WalkHome(home)
        self.quiz = Quiz(home)
        self.radio = Radio(home)
        from . import campus, housemates as hm, kitchen, personal, safety_plus
        from .points import Points

        self.points = Points(home)
        self.noise, self.bathroom, self.bins = hm.Noise(home), hm.Bathroom(home), hm.Bins(home)
        self.borrow, self.rules, self.referee = hm.Borrow(home), hm.Rules(home), hm.Referee(home)
        self.locked_out, self.polls, self.thermostat = hm.LockedOut(home), hm.Polls(home), hm.Thermostat(home)
        self.calendar, self.errands = hm.HouseCalendar(home), hm.Errands(home)
        self.move_in, self.secrets = hm.MoveIn(home), hm.Secrets(home)
        self.deadlines, self.together, self.reader = campus.Deadlines(home), campus.Together(home), campus.Reader(home)
        self.vision, self.exam_kit, self.projects = campus.Vision(home), campus.ExamKit(home), campus.Projects(home)
        self.grades, self.hangover = campus.Grades(home), campus.Hangover(home)
        self.shower_slots, self.wear = campus.ShowerSlots(home), campus.Wear(home)
        self.kitchen, self.stove = kitchen.Kitchen(home), kitchen.Stove(home)
        self.takeout, self.bets = kitchen.Takeout(home), kitchen.Bets(home)
        self.night_out, self.safe_word = safety_plus.NightOut(home), safety_plus.SafeWord(home)
        self.ring_phone, self.network = safety_plus.RingPhone(home), safety_plus.Network(home)
        self.head_count = safety_plus.HeadCount(home)
        self.missed, self.my_calendar, self.things = personal.Missed(home), personal.MyCalendar(home), personal.Things(home)
        self.place_reminders, self.nicknames = personal.PlaceReminders(home), personal.Nicknames(home)
        self.usual, self.tv, self.mood_dial = personal.Usual(home), personal.TVTimer(home), personal.MoodDial(home)
        self.blitz, self.semester = personal.Blitz(home), personal.SemesterBreak(home)
        self.horoscope, self.year = personal.Horoscope(home), personal.YearReview(home)
        # the order sentences are tried in: specific first, then house points, then facts about people
        self.parts = [self.kitchen, self.takeout, self.bets, self.bins, self.bathroom, self.borrow, self.calendar,
                      self.things, self.place_reminders, self.nicknames, self.usual, self.tv, self.rules, self.referee,
                      self.locked_out, self.polls, self.thermostat, self.errands, self.move_in, self.secrets,
                      self.deadlines, self.together, self.reader, self.projects, self.grades, self.hangover,
                      self.shower_slots, self.wear, self.noise, self.night_out, self.ring_phone, self.head_count,
                      self.missed, self.my_calendar, self.blitz, self.semester, self.horoscope, self.year,
                      self.exam_kit, self.stove, self.network, self.mood_dial, self.points]
        self.sync = False  # tests: handle events straight away
        self._q: queue.Queue = queue.Queue()
        self._running = False
        home.events.subscribe(self._event)
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------- events and the clock ----------
    def _event(self, ev: dict) -> None:
        if ev.get("live"):
            return
        if self.sync:
            self.handle(ev)
        else:
            self._q.put(ev)

    def _worker(self) -> None:
        while True:
            ev = self._q.get()
            if ev is None:
                return
            self.handle(ev)

    def handle(self, ev: dict) -> None:
        for part in (self.party, self.exams, self.watch, self.walk, *self.parts):
            try:
                part.on_event(ev)
            except Exception as e:
                log.error("%s: %s", type(part).__name__.lower(), e)

    def tick(self, now=None) -> None:
        from . import clock

        now = now or clock.now()
        for name, fn in (("study", self.study.tick), ("party", self.party.tick), ("walk home", self.walk.tick),
                         ("radio", self.radio.tick), ("exams", lambda: self.exams.tick(now)),
                         ("meeting", lambda: self.meeting.tick(now)), ("watch", lambda: self.watch.tick(now)),
                         ("quiz", lambda: self.quiz.tick(now)), ("door status", self.door_status.refresh)):
            try:
                fn()
            except Exception as e:
                log.error("%s: %s", name, e)
        for part in self.parts:
            try:
                part.tick(now)
            except Exception as e:
                log.error("%s: %s", type(part).__name__.lower(), e)

    def start(self) -> None:
        self._running = True
        self.walk.start_listening()  # the house's ntfy topic: messages from your phones
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False
        self.walk.stop()

    def close(self) -> None:
        self.stop()
        self.home.events.unsubscribe(self._event)
        self._q.put(None)

    def _loop(self) -> None:
        while self._running:
            self.tick()
            time.sleep(2)

    # ---------- commands ({life: party_start}) ----------
    def do(self, what: str, room: str | None = None, person: str | None = None, action: dict | None = None) -> str:
        a = action or {}
        p = person if person in self.home.instances.all else None
        speaker = self.home.instances.all.get(p) if p else self.home.instances.for_room(room)
        table = {
            "party_start": lambda: self.party.start(room, p),
            "party_stop": lambda: self.party.stop(),
            "party_wifi": lambda: self.party.wifi() or self.home.voice.speak("party_not_on", room=room),
            "party_quiet": lambda: self.party.quiet_when(room),
            "study_on": lambda: self.study.start(room, p, a.get("minutes")),
            "study_off": lambda: self.study.stop(room),
            "study_skip": lambda: self.study.skip_break(room),
            "study_left": lambda: self.study.left(room),
            "meeting_book": lambda: self.meeting.book(by=getattr(speaker, "id", None)),
            "meeting_cancel": lambda: self.meeting.cancel(),
            "meeting_when": lambda: self.meeting.when(),
            "meeting_agenda": lambda: self.meeting.read_agenda(room),
            "shower_start": lambda: self.watch.shower_start(p),
            "shower_stop": lambda: self.watch.shower_stop("said so"),
            "shower_record": lambda: self.watch.say_shower_record(room),
            "quiz_start": lambda: self.quiz.start(),
            "quiz_next": lambda: self.quiz.next(),
            "quiz_stop": lambda: self.quiz.stop(),
            "radio_on": lambda: self.radio.start(),
            "radio_off": lambda: self.radio.stop(),
            "windows_advice": lambda: self.watch.window_advice(room),
            "exams_list": lambda: self._exams_list(room),
            "walk_status": lambda: self.walk.heard("who's walking home", room=room) or "",
            "door_status": lambda: self.door_status.set(room if speaker is None or speaker.main or not speaker.rooms
                                                        else speaker.rooms[0], str(a.get("status", "clear"))),
            "safe_word": lambda: self.safe_word.trigger(room, p),
            "quick": lambda: "done" if self.quick(str(a.get("_phrase") or a.get("text") or ""), room, p)
            else self.home.voice.speak("didnt_catch", room=room),
        }
        if what not in table:
            raise ValueError(f"unknown house life action '{what}' (have: {', '.join(table)})")
        return str(table[what]() or "")

    def _exams_list(self, room) -> str:
        text = self.exams.spoken_list()
        return self.home.voice.speak("exam_list" if text else "exam_none", room=room, list=text)

    # ---------- sentences with details, before the AI ----------
    def quick(self, said: str, room: str | None = None, person: str | None = None, strict: bool = False,
              reply=None) -> bool:
        """True if one of the house life features understood it (and has answered). strict: said in their
        own room without the house's name, so remarks about people aren't saved as facts."""
        h = self.home
        s = said.lower().replace("’", "'").strip(" .!?")
        speaker = h.instances.all.get(person) if person in h.instances.all else h.instances.for_room(room)
        if self.party.active and re.match(r"^(?:what's|what is)(?: the)? (?:guest )?wi-?fi(?: password)?$", s):
            self.party.wifi()
            return True
        if re.match(r"^(?:when's|when is|what time is) quiet time|^how long (?:until|till|til) quiet time", s):
            self.party.quiet_when(room)
            return True
        if self.study.studying(room) and re.match(r"^how (?:long|much longer)(?: left| until my break| till my break)?$", s):
            self.study.left(room)
            return True
        if re.match(r"^should (?:i|we) open (?:a|the|some) windows?|^(?:is|how's|how is|what's) the pollen", s):
            self.watch.window_advice(room)
            return True
        handlers = [lambda: self.walk.heard(said, speaker, room), lambda: self.meeting.heard(said),
                    lambda: self.door_status.heard(said, room, speaker), lambda: self.exams.heard(said, speaker)]
        handlers += [lambda p=p: p.heard(said, room, speaker) for p in self.parts
                     if not (strict and p in (self.points, self.things, self.borrow, self.secrets, self.nicknames))]
        if not strict:
            handlers.append(lambda: self.people.heard(said, speaker))
        for fn in handlers:
            got = fn()
            if got is None:
                continue
            if isinstance(got, tuple):
                key, values = got
                if reply is not None:
                    reply(h.voice.resolve(key, **values)[0])
                else:
                    h.voice.speak(key, room=room, **values)
            return True
        return False

    def from_phone(self, text: str, title: str = "") -> bool:
        """A message on the house's ntfy topic: "Finn locked out", "vote pizza" (title: Finn), "Parker walking home 20"."""
        text = (text or "").strip()
        if not text:
            return False
        if self.walk.heard(text, title=title) is not None:
            return True
        who = self.people.find(title) if title else None
        if who is None:
            m = re.match(rf"^({self.points.who_rx()})[:,]?\s+(.+)$", text.lower())
            if m:
                who, text = self.people.find(m.group(1)), m.group(2)
        if who is None:
            return False
        if re.match(r"^(?:locked out|i'm locked out|let me in)$", text.lower()):
            self.locked_out.start(who)
            return True
        if re.match(r"^(?:safe word|help|sos)$", text.lower()):
            self.safe_word.trigger(None, who.id)
            return True
        return self.quick(text, person=who.id, reply=lambda msg: self.home.notify_person(who, msg, title="House"))

    # ---------- for the AI ----------
    def context_line(self) -> str:
        from . import clock

        bits = []
        people = self.people.context_line()
        if people:
            bits.append(people)
        ex = self.exams.all(7)
        if ex:
            bits.append("Exams coming up: " + "; ".join(f"{i.name} {e['subject']} {e['at'].strftime('%a %d %b %H:%M')}"
                                                       for i, e in ex[:6]) + ".")
        doors = {r: s for r, s in self.door_status.all().items() if s != "free"}
        if doors:
            bits.append("Bedroom doors: " + ", ".join(f"{r} {s}" for r, s in doors.items()) + ".")
        st = self.party.status()
        if st:
            bits.append(f"A party is on; quiet time {st['quiet_at']}" + (f" ({st['why']})" if st["why"] else "") + ".")
        b = self.meeting.booked()
        if b:
            bits.append(f"House meeting booked {b['at'].replace('T', ' ')} in the {b['room']}.")
        walking = self.walk.status()
        if walking:
            bits.append("Walking home: " + ", ".join(f"{w['name']} due {w['eta']}" for w in walking) + ".")
        if self.exams.quiet_now():
            bits.append("Exam quiet hours are on right now.")
        _ = clock
        return " ".join(bits)

    SYSTEM = ("The people who live here: what the house knows about each of them is given with each message. "
              "Respect diets and allergies in anything you suggest (meals, recipes, party food, shopping). When "
              "someone tells you something lasting about a housemate or themselves (diet, allergies, likes, habits), "
              "save it with the housemates tool. Exams go in with the exams tool; someone walking home with walk_home.")

    def tools(self, inst) -> list[dict]:
        from .people import tool_spec

        out = [tool_spec()]
        names = [i.name for i in self.home.instances.all.values() if not i.main]
        out.append({"name": "exams", "description": "Someone's exams: add one (the house goes quiet the night before), "
                    "list what's coming up, or remove one.", "input_schema": {
                        "type": "object", "additionalProperties": False, "required": ["action", "person", "subject", "when"],
                        "properties": {"action": {"type": "string", "enum": ["add", "list", "remove"]},
                                       "person": {"type": "string", "description": f"One of {', '.join(names)}, or empty for the speaker."},
                                       "subject": {"type": "string"},
                                       "when": {"type": "string", "description": "Local time YYYY-MM-DD HH:MM, or empty."}}}})
        out.append({"name": "walk_home", "description": "Walk-home mode: someone is on their way home; if they're not "
                    "home in time, the others are told. start, extend (minutes more), cancel, arrived, status.",
                    "input_schema": {"type": "object", "additionalProperties": False,
                                     "required": ["action", "person", "minutes", "place"],
                                     "properties": {"action": {"type": "string", "enum": ["start", "extend", "cancel", "arrived", "status"]},
                                                    "person": {"type": "string"},
                                                    "minutes": {"type": "number", "description": "0 if not given."},
                                                    "place": {"type": "string", "description": "Where from, or empty."}}}})
        out.append({"name": "house_meeting", "description": "House meetings: find and book a time when everyone's "
                    "home, cancel, say when, or add to / read the agenda.", "input_schema": {
                        "type": "object", "additionalProperties": False, "required": ["action", "item"],
                        "properties": {"action": {"type": "string", "enum": ["book", "cancel", "when", "agenda_add", "agenda"]},
                                       "item": {"type": "string", "description": "For agenda_add; else empty."}}}})
        return out

    TOOL_NAMES = {"housemates", "exams", "walk_home", "house_meeting"}

    def run_tool(self, name: str, args: dict, room: str | None, inst) -> Any:
        import json

        if name == "housemates":
            from .people import run_tool

            return run_tool(self.home, args, inst)
        if name == "exams":
            who = self.people.find(args.get("person") or "") if args.get("person") else inst
            if args["action"] == "list":
                return json.dumps([{"person": i.name, "subject": e["subject"], "at": e["at"].strftime("%Y-%m-%d %H:%M")}
                                   for i, e in self.exams.all(21)])
            if who is None or who.main:
                raise KeyError("whose exam?")
            if args["action"] == "remove":
                return f"removed {self.exams.remove(who, args.get('subject') or '')}"
            from .study import _parse_at

            at = _parse_at(args["when"])
            self.exams.add(who, args.get("subject") or "exam", at)
            return f"saved {who.name}'s {args.get('subject') or 'exam'} for {at.strftime('%a %d %b %H:%M')}"
        if name == "walk_home":
            act = args["action"]
            if act == "status":
                return json.dumps(self.walk.status())
            who = args.get("person") or inst
            if act == "start":
                return self.walk.start(who, args.get("minutes") or None, args.get("place") or "",
                                       by=getattr(inst, "id", None), room=room)
            if act == "extend":
                return self.walk.extend(who, float(args.get("minutes") or 10))
            if act == "cancel":
                return self.walk.cancel(who)
            return "home safe" if self.walk.arrived(who) else "they weren't walking home"
        if name == "house_meeting":
            act = args["action"]
            if act == "agenda_add":
                return self.meeting.add(args.get("item") or "")
            return {"book": lambda: self.meeting.book(by=getattr(inst, "id", None)), "cancel": self.meeting.cancel,
                    "when": self.meeting.when, "agenda": lambda: self.meeting.read_agenda(room)}[act]()
        raise KeyError(name)

    # ---------- the /house page ----------
    def status(self) -> dict[str, Any]:
        insts = self.home.instances
        study = [{"room": r, "person": s["person"], "phase": s["phase"],
                  "minutes_left": max(0, round((s["until"] - time.time()) / 60)), "blocks": s["blocks"]}
                 for r, s in self.study.sessions.items()]
        b = self.meeting.booked()
        rec = self.watch.shower_record(7)
        return {
            "people": [{"id": i.id, "name": i.name, "facts": self.people.facts(i),
                        "diet": insts.diet(i, i.diet)} for i in self.people.housemates()],
            "party": self.party.status(), "study": study, "doors": self.door_status.all(),
            "exams": [{"person": i.name, "subject": e["subject"], "at": e["at"].strftime("%a %d %b %H:%M")}
                      for i, e in self.exams.all(14)],
            "exam_quiet": self.exams.quiet_now(),
            "meeting": {"at": b["at"], "room": b["room"]} if b else None,
            "agenda": self.meeting.agenda(), "talking_points": self.meeting.talking_points(),
            "walking": self.walk.status(),
            "shower": {"running": bool(self.watch.shower), "record": {"name": self.watch.name(rec[0]), "minutes": rec[1]}
                       if rec else None},
            "last_out": {self.watch.name(k): v for k, v in self.watch.last_out_counts(30).items()},
            "quiz": {"running": self.quiz.running, "last": self.quiz.last},
            "radio": {"on": self.radio.on, "segments": self.radio.segments},
        }


def life_for(home) -> HouseLife:
    life = getattr(home, "life", None)
    return life if life is not None else HouseLife(home)
