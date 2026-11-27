"""Uni life: deadlines, studying together, word counts, readings, slides, group projects, grades,
hangovers, shower slots and what to wear.

Deadline crunch   deadlines from your calendar (due, deadline, assignment, essay, submission) or said:
                  "my essay's due Friday at 5", "the marketing report is due Monday, 2000 words".
                  In the last 24 hours: your door says busy, no announcements in your room, and an hourly
                  "how's the essay going?". "I'm at 1200 words" keeps the pace: "you need 400 a day".
Study together    "join Parker's study session", "study together in the lounge" (the others are invited);
                  everyone in the session hears the breaks.
Readings aloud    a PDF, web page or text on /house (or "read this" from the laptop companion) is read to you
                  in your room. "pause reading", "keep reading", "skip that bit", "stop reading".
Slides and photos "explain this slide" / any photo question from your phone on /house, or the companion's
                  screen: the AI looks and explains (a vision model: Claude, or local_ai vision_model).
Exam-morning kit  an hour before an exam: "student card, calculator, pens, water?" in your room and on your phone.
Group projects    "group project: marketing report due Friday with Sam and Lee", "I'm doing the intro",
                  "Sam's doing the survey", "intro's done", "what's left on the marketing report?"
Grades            "my calc grades are assignments 85 worth 40 percent and midterm 70 worth 20 percent",
                  "what do I need on the final to get a B in calc?"
Hangover mode     "I'm hungover": quiet, do not disturb, water reminders every half hour, gentle mockery.
Shower slots      the night before, if several alarms are close together: everyone gets a shower time so
                  nobody's fighting for the bathroom at 8.
What to wear      "what should I wear?": today's weather and what's on your calendar.

deadlines: { checkin_minutes: 60, crunch_hours: 24 }
exams: { kit: [student card, calculator, pens, water] }
grades: { scale: { A: 90, B: 80, C: 70, D: 60 } }    # or HD: 85, D: 75, C: 65, P: 50
showers: { plan_at: "21:00", minutes: 12 }
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import clock
from .life_base import Part, clean, join

DUE_WORDS = re.compile(r"\b(due|deadline|assignment|essay|report|submission|submit|hand in|lab report|portfolio)\b", re.I)


def _when(at: datetime) -> str:
    today = clock.now().date()
    day = "today" if at.date() == today else "tomorrow" if at.date() == today + timedelta(days=1) else at.strftime("%A")
    return f"{day} at {clock.spoken_time(at)}"


# ================================================================ deadlines + word counts
class Deadlines(Part):
    section = "deadlines"
    store = "deadlines"

    def all(self, days: float = 21) -> list[tuple]:
        now = clock.now()
        out = []
        saved = self.load({"items": []})["items"]
        for d in saved:
            at = datetime.fromisoformat(d["due"])
            if now - timedelta(hours=2) <= at <= now + timedelta(days=days):
                out.append((self.home.instances.all.get(d["person"]), d))
        life = getattr(self.home, "life", None)
        for inst in self.housemates():
            if not inst.calendar:
                continue
            try:
                from .calendar_feed import upcoming

                for e in upcoming(inst.calendar, days=int(days)):
                    if DUE_WORDS.search(e["summary"]) and not (life and _is_exam(e["summary"])):
                        at = datetime.strptime(e["start"][:16], "%Y-%m-%d %H:%M").astimezone() if not e["all_day"] \
                            else datetime.fromisoformat(e["start"]).replace(hour=23, minute=59).astimezone()
                        if not any(x["what"].lower() == e["summary"].lower() for i, x in out if i and i.id == inst.id):
                            out.append((inst, {"id": "cal", "person": inst.id, "what": e["summary"], "due": at.isoformat(),
                                               "words": None, "target": None}))
            except Exception:
                pass
        return sorted([(i, d) for i, d in out if i], key=lambda x: x[1]["due"])

    def add(self, inst, what: str, due: datetime, target: int | None = None) -> dict:
        d = self.load({"items": []})
        item = {"id": uuid.uuid4().hex[:6], "person": inst.id, "what": what, "due": due.isoformat(timespec="minutes"),
                "target": target, "words": None, "checked": 0}
        d["items"] = [x for x in d["items"] if datetime.fromisoformat(x["due"]) > clock.now() - timedelta(days=7)] + [item]
        self.save(d)
        self.home.events.log("deadline.added", person=inst.id, what=what, due=item["due"])
        return item

    def nearest(self, inst) -> dict | None:
        mine = [d for i, d in self.all() if i.id == inst.id and d["id"] != "cal"]
        return mine[0] if mine else None

    def words(self, inst, n: int, target: int | None = None) -> tuple[str, dict] | None:
        d = self.load({"items": []})
        mine = sorted((x for x in d["items"] if x["person"] == inst.id and datetime.fromisoformat(x["due"]) > clock.now()),
                      key=lambda x: x["due"])
        if not mine:
            return "words_no_deadline", {"words": n}
        item = mine[0]
        item["words"] = n
        if target:
            item["target"] = target
        self.save(d)
        return self.pace(inst, item)

    def pace(self, inst, item) -> tuple[str, dict]:
        due = datetime.fromisoformat(item["due"])
        days = max((due - clock.now()).total_seconds() / 86400, 0.05)
        if not item.get("target") or item.get("words") is None:
            return "deadline_left", {"what": item["what"], "left": clock.spoken_duration(max(0, (due - clock.now()).total_seconds()))}
        left = max(0, int(item["target"]) - int(item["words"]))
        if not left:
            return "words_done", {"what": item["what"], "words": item["words"]}
        per_day = round(left / days) if days >= 1 else left
        return "words_pace", {"what": item["what"], "words": item["words"], "left_words": left,
                              "per_day": per_day, "when": _when(due)}

    def crunching(self, inst) -> dict | None:
        hrs = float(self.cfg.get("crunch_hours", 24))
        now = clock.now()
        for i, d in self.all(2):
            if i.id == inst.id and now <= datetime.fromisoformat(d["due"]) <= now + timedelta(hours=hrs):
                return d
        return None

    def tick(self, now):
        life = self.home.life
        for inst in self.housemates():
            d = self.crunching(inst)
            room = self.room_of(inst)
            if not room:
                continue
            st = life.door_status.manual.get(room, ("", 0))[0]
            if d and st != "busy":
                life.door_status.manual[room] = ("busy", datetime.fromisoformat(d["due"]).timestamp())
                self.home.instances.set_dnd(inst, True)
                self.say("deadline_crunch", room=room, what=d["what"], when=_when(datetime.fromisoformat(d["due"])))
            if not d:
                continue
            every = float(self.cfg.get("checkin_minutes", 60)) * 60
            key = f"_checked_{inst.id}"
            if time.time() - getattr(self, key, time.time()) >= every and 9 <= now.hour <= 23:
                setattr(self, key, time.time())
                k, v = self.pace(inst, d)
                self.say("deadline_checkin", room=room, what=d["what"], status=self.text(k, **v))
            elif not hasattr(self, key):
                setattr(self, key, time.time())

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        if me is None:
            return None
        from .study import parse_when

        m = re.match(r"^(?:my |the |our |i have an? |i've got an? )?(.+?) (?:is |'s |are )?due (.+?)(?:,? (\d[\d,]*) words?)?$", s) \
            or re.match(r"^(?:my |the )?(.+?)'s due (.+?)(?:,? (\d[\d,]*) words?)?$", s)
        if m and not re.match(r"^(?:rent|bins?|the rent|what|when|which|who|anything)\b", m.group(1)):
            due = parse_when(" " + m.group(2))
            if due is None:
                return None
            if not re.search(r"\b(?:at|am|pm|\d:\d\d)\b", m.group(2)):
                due = due.replace(hour=23, minute=59)
            target = int(m.group(3).replace(",", "")) if m.group(3) else None
            what = re.sub(r"'s$", "", m.group(1)).strip()
            self.add(me, what, due, target)
            return "deadline_saved", {"what": what, "when": _when(due)}
        m = re.match(r"^i'?m (?:at|on|up to) (\d[\d,]*) words(?: (?:of|out of) (\d[\d,]*))?", s) or \
            re.match(r"^(?:i've|i have) (?:written|done) (\d[\d,]*) words(?: (?:of|out of) (\d[\d,]*))?", s)
        if m:
            return self.words(me, int(m.group(1).replace(",", "")), int(m.group(2).replace(",", "")) if m.group(2) else None)
        if re.match(r"^how am i (?:going|doing) (?:on|with) (?:my|the) (\w+)|^what deadlines|^when's my (?:next )?deadline", s):
            item = self.nearest(me)
            if not item:
                return "deadline_none", {}
            return self.pace(me, item)
        return None


def _is_exam(summary: str) -> bool:
    from .study import EXAM_WORDS

    return bool(EXAM_WORDS.search(summary))


# ================================================================ study together, readings aloud, slides
class Together(Part):
    def heard(self, said, room, speaker):
        s = clean(said)
        study = self.home.life.study
        m = re.match(rf"^(?:join|study with) ({self.who_rx()})(?:'s)?(?: study session)?$", s)
        if m:
            other = self.find(m.group(1))
            host = next((r for r, st in study.sessions.items() if st["person"] == other.id), None)
            if host is None:
                return "together_none", {"name": other.name}
            study.join(host, room or "")
            return "together_joined", {"name": other.name}
        if re.match(r"^(?:study together|group study|let's study together)(?: in the (?:lounge|living room|kitchen))?$", s):
            where = (self.shared_rooms() or [room])[0]
            study.start(where, getattr(speaker, "id", None))
            if room and room != where:
                study.join(where, room)
            self.tell(self.others(speaker if speaker and not speaker.main else None), "together_invite",
                      title="Study session", name=getattr(speaker, "name", "Someone"), room=where)
            return "done"
        return None


class Reader(Part):
    store = "reading"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.active: dict[str, dict] = {}  # room -> {paras, i, paused, title}

    def load_text(self, data: bytes | str, filename: str = "", url: str = "") -> str:
        if url:
            import requests

            r = requests.get(url, timeout=20, headers={"User-Agent": "phrasehome"})
            html = r.text
            html = re.sub(r"(?is)<(script|style|nav|header|footer|aside).*?</\1>", " ", html)
            text = re.sub(r"(?s)<[^>]+>", "\n", html)
            return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()
        if isinstance(data, bytes) and (filename.lower().endswith(".pdf") or data[:4] == b"%PDF"):
            from .housemates import _pdf_text

            return _pdf_text(data)
        return data.decode("utf-8", "replace") if isinstance(data, bytes) else data

    def start(self, room: str | None, text: str, title: str = "that") -> str:
        paras = [p.strip() for p in re.split(r"\n\s*\n|(?<=[.!?])\s{2,}", text) if len(p.strip()) > 2]
        if not paras:
            raise ValueError("nothing to read")
        room = (room or self.home.default_room() or "").lower()
        self.active[room] = {"paras": paras, "i": 0, "paused": False, "title": title, "gen": time.time()}
        threading.Thread(target=self._read, args=(room, self.active[room]["gen"]), daemon=True).start()
        return f"reading {len(paras)} paragraphs"

    def _read(self, room: str, gen: float) -> None:
        v = self.home.voice
        while True:
            st = self.active.get(room)
            if not st or st["gen"] != gen or st["paused"] or st["i"] >= len(st["paras"]):
                if st and st["i"] >= len(st["paras"]):
                    self.active.pop(room, None)
                    v.speak("reading_done", room=room, title=st["title"])
                return
            text = st["paras"][st["i"]]
            for chunk in _chunks(text, 400):
                v.speak(chunk, room=room, emotion="calm")
                if getattr(v, "_hushed", False):  # "shut up": pause where it was
                    st["paused"] = True
                    return
            st["i"] += 1

    def heard(self, said, room, speaker):
        s = clean(said)
        st = self.active.get((room or "").lower())
        if st is None and not re.match(r"^read (?:this|that|it) (?:to me|out|aloud)", s):
            return None
        if re.match(r"^(?:pause|stop|hold) (?:the )?reading$|^pause that$", s) and st:
            st["paused"] = True
            return "reading_paused", {}
        if re.match(r"^(?:keep|continue|resume|carry on) reading$|^(?:keep going|continue)$", s) and st:
            st["paused"], st["gen"] = False, time.time()
            threading.Thread(target=self._read, args=((room or "").lower(), st["gen"]), daemon=True).start()
            return "done"
        if re.match(r"^skip (?:that|this|that bit|this bit|ahead)$|^next (?:paragraph|bit)$", s) and st:
            st["i"] += 1
            st["paused"], st["gen"] = False, time.time()
            threading.Thread(target=self._read, args=((room or "").lower(), st["gen"]), daemon=True).start()
            return "done"
        if re.match(r"^stop reading(?: altogether)?$|^close the reading$", s) and st:
            self.active.pop((room or "").lower(), None)
            return "reading_stopped", {}
        return None


def _chunks(text: str, n: int) -> list[str]:
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(cur) + len(sent) > n and cur:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    return out + ([cur] if cur else [])


class Vision(Part):
    PROMPTS = {
        "slide": "This is a lecture slide. Explain what it's saying in plain words for a tired student, in four short "
                 "spoken sentences, then ask one quick question to check they got it. No lists or markdown.",
        "screen": "This is a screenshot of someone's laptop. Answer their question about it in three short spoken "
                  "sentences. If there's an error message, say what it means and the likely fix.",
        "photo": "Answer the question about this photo in two or three short spoken sentences.",
    }

    def ask(self, jpeg: bytes, question: str = "", kind: str = "photo", room: str | None = None,
            person: str | None = None, speak: bool = True) -> str:
        from . import ai

        prompt = self.PROMPTS.get(kind, self.PROMPTS["photo"]) + (f"\nTheir question: {question}" if question else "")
        people = getattr(self.home, "people", None)
        if people is not None:
            line = people.context_line()
            if line:
                prompt += "\n" + line
        text = ai.ask(self.home, prompt, images=[_jpeg(jpeg)], max_tokens=800)
        text = str(text).strip()
        self.home.events.log("vision.asked", kind=kind, person=person or "", question=question[:120])
        if speak and text:
            self.home.voice.speak(text, room=room)
        return text


def _jpeg(data: bytes) -> bytes:
    if data[:3] == b"\xff\xd8\xff":
        return data
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((1600, 1600))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=85)
        return out.getvalue()
    except Exception:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        return cv2.imencode(".jpg", img)[1].tobytes()


# ================================================================ exam-morning kit
class ExamKit(Part):
    def __init__(self, home) -> None:
        super().__init__(home)
        self._told: set[str] = set()

    def tick(self, now):
        ex = self.home.life.exams
        kit = (self.home.config.extra.get("exams") or {}).get("kit") or ["student card", "calculator", "pens", "water"]
        for inst, e in ex.today(now):
            key = f"{inst.id}:{e['at'].isoformat()}"
            mins = (e["at"] - now).total_seconds() / 60
            if 0 < mins <= 60 and key not in self._told:
                self._told.add(key)
                vals = dict(name=inst.name, kit=join(kit), subject=e["subject"])
                if inst.id in self.home.state.home_people() or not self.home.state.presence:
                    self.say("exam_kit", room=self.room_of(inst), **vals)
                self.tell([inst], "exam_kit", title="Exam kit", **vals)


# ================================================================ group projects
class Projects(Part):
    store = "projects"

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        if me is None:
            return None
        d = self.load({"projects": []})
        from .study import parse_when

        m = re.match(r"^(?:new )?group project[:,]? (?:the )?(.+?) due (.+?)(?: with (.+))?$", s)
        if m:
            due = parse_when(" " + m.group(2))
            members = [x.strip() for x in re.split(r",| and ", m.group(3) or "") if x.strip()]
            d["projects"].append({"id": uuid.uuid4().hex[:6], "owner": me.id, "name": m.group(1), "members": members,
                                  "due": due.isoformat(timespec="minutes") if due else "", "tasks": []})
            self.save(d)
            return "project_saved", {"name": m.group(1), "when": _when(due) if due else "no date yet"}
        mine = [p for p in d["projects"] if p["owner"] == me.id]
        if not mine:
            return None
        m = re.match(r"^(i'm|i am|\w+(?:'s| is)) doing (?:the )?(.+?)(?: for (?:the )?(.+))?$", s)
        if m:
            p = _pick(mine, m.group(3))
            who = me.name if m.group(1) in ("i'm", "i am") else re.sub(r"(?:'s| is)$", "", m.group(1)).title()
            p["tasks"].append({"task": m.group(2), "who": who, "done": False})
            self.save(d)
            return "project_task", {"who": who, "task": m.group(2), "name": p["name"]}
        m = re.match(r"^(?:the )?(.+?)(?:'s| is) (?:done|finished)$|^i(?:'ve| have)? finished (?:the )?(.+)$", s)
        if m:
            what = m.group(1) or m.group(2)
            for p in mine:
                for t in p["tasks"]:
                    if not t["done"] and (what in t["task"] or t["task"] in what):
                        t["done"] = True
                        self.save(d)
                        left = [x for x in p["tasks"] if not x["done"]]
                        return "project_task_done", {"task": t["task"], "left": len(left), "name": p["name"]}
            return None
        m = re.match(r"^what's left (?:on|for) (?:the |my )?(.+?)(?: project)?$|^(?:group )?project status$", s)
        if m:
            p = _pick(mine, m.group(1) if m and m.lastindex else None)
            left = [f"{t['task']} ({t['who']})" for t in p["tasks"] if not t["done"]]
            return ("project_left", {"name": p["name"], "items": join(left)}) if left else ("project_clear", {"name": p["name"]})
        return None

    def tick(self, now):
        d = self.load({"projects": []})
        changed = False
        for p in d["projects"]:
            if not p.get("due") or p.get("reminded"):
                continue
            due = datetime.fromisoformat(p["due"])
            if 0 < (due - now).total_seconds() <= 86400:
                p["reminded"] = changed = True
                owner = self.home.instances.all.get(p["owner"])
                left = [f"{t['task']} ({t['who']})" for t in p["tasks"] if not t["done"]]
                self.tell([owner], "project_due", title="Group project", name=p["name"], items=join(left) or "nothing")
        if changed:
            self.save(d)


def _pick(projects: list[dict], name: str | None) -> dict:
    if name:
        for p in projects:
            if name in p["name"] or p["name"] in name:
                return p
    return projects[-1]


# ================================================================ grades
class Grades(Part):
    section = "grades"
    store = "grades"

    def scale(self) -> dict[str, float]:
        return {str(k).lower(): float(v) for k, v in (self.cfg.get("scale") or {"A": 90, "B": 80, "C": 70, "D": 60}).items()}

    def heard(self, said, room, speaker):
        s = clean(said).replace("%", " percent")
        me = speaker if speaker is not None and not speaker.main else None
        if me is None:
            return None
        d = self.load({})
        mine = d.setdefault(me.id, {})
        m = re.match(r"^my (.+?) (?:grades|marks|results)(?: are| so far)?[:,]? (.+)$", s)
        if m:
            course = m.group(1)
            parts = re.findall(r"([a-z][a-z ]*?) (\d+(?:\.\d+)?)(?: percent)? (?:worth|weighted|out of a weight of) (\d+(?:\.\d+)?)", m.group(2))
            if not parts:
                return None
            c = mine.setdefault(course, {"parts": {}, "final": None})
            for name, score, weight in parts:
                c["parts"][name.strip().replace("and ", "")] = [float(score), float(weight)]
            done = sum(w for _, w in c["parts"].values())
            c["final"] = c["final"] or max(0.0, 100 - done)
            self.save(d)
            return "grades_saved", {"course": course, "now": f"{_sofar(c):.0f}", "final": f"{c['final']:g}"}
        m = re.match(r"^(?:the )?final (?:in |for )?(.+?)? ?is worth (\d+)", s)
        if m and mine:
            course = m.group(1) or next(iter(mine))
            if course in mine:
                mine[course]["final"] = float(m.group(2))
                self.save(d)
                return "grades_final", {"course": course, "final": m.group(2)}
        m = re.match(r"^what do i need (?:on|in) the (?:final|exam)(?: exam)? to (?:get|pass with) (?:an? )?([a-z]{1,2}|\d+(?: percent)?)(?: in (.+))?$", s)
        if m:
            if not mine:
                return "grades_none", {}
            course = m.group(2) if m.group(2) in mine else next(iter(mine))
            c = mine[course]
            want = m.group(1).replace(" percent", "")
            target = float(want) if want.replace(".", "").isdigit() else self.scale().get(want)
            if target is None or not c.get("final"):
                return "grades_none", {}
            need = (target - sum(sc * w / 100 for sc, w in c["parts"].values())) / (c["final"] / 100)
            if need <= 0:
                return "grades_already", {"course": course, "grade": want.upper()}
            if need > 100:
                return "grades_impossible", {"course": course, "grade": want.upper(), "need": f"{need:.0f}"}
            return "grades_need", {"course": course, "grade": want.upper(), "need": f"{need:.0f}"}
        return None


def _sofar(c: dict) -> float:
    w = sum(w for _, w in c["parts"].values())
    return sum(sc * w_ for sc, w_ in c["parts"].values()) / w if w else 0.0


# ================================================================ hangover mode
class Hangover(Part):
    section = "hangover"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.active: dict[str, dict] = {}

    def start(self, room: str, inst) -> None:
        self.active[room] = {"person": getattr(inst, "id", ""), "next": time.time() + 30 * 60, "left": 6,
                             "dnd": self.home.instances.dnd(inst) if inst else False}
        if inst is not None and not inst.main:
            self.home.instances.set_dnd(inst, True)
        if self.cfg.get("lights"):
            threading.Thread(target=self.home.run_steps, args=(self.cfg["lights"], {"here": room}, "hangover"), daemon=True).start()
        self.home.events.log("hangover", room=room, person=getattr(inst, "id", ""))

    def stop(self, room: str) -> bool:
        st = self.active.pop(room, None)
        if st:
            inst = self.home.instances.all.get(st["person"])
            if inst:
                self.home.instances.set_dnd(inst, st["dnd"])
        return bool(st)

    def tick(self, now):
        for room, st in list(self.active.items()):
            if time.time() >= st["next"]:
                st["left"] -= 1
                st["next"] = time.time() + 30 * 60
                self.home.voice.speak("hangover_water", room=room, emotion="soft")
                if st["left"] <= 0:
                    self.stop(room)

    def heard(self, said, room, speaker):
        s = clean(said)
        if re.match(r"^(?:i'm|i am|i feel) (?:so |really |very )?(?:hungover|hung over|seedy|dusty|rough as)|^hangover mode(?: on)?$", s):
            self.start((room or "").lower(), speaker)
            return "hangover_on", {"name": getattr(speaker, "name", "")}
        if re.match(r"^hangover mode off$|^i'm (?:feeling )?(?:better|alive|human) (?:now|again)$", s):
            if self.stop((room or "").lower()):
                return "hangover_off", {}
        return None


# ================================================================ shower slots
class ShowerSlots(Part):
    section = "showers"
    store = "shower_slots"

    def plan(self, now=None) -> list[tuple]:
        now = now or clock.now()
        day = (now + timedelta(days=1)).date() if now.hour >= 12 else now.date()
        length = float(self.cfg.get("minutes", 12))
        avg = self._averages()
        wakes = []
        for inst in self.housemates():
            for a in self.home.instances.items(inst, ("alarm",)):
                at = datetime.fromisoformat(a["due"])
                if at.date() == day and not a.get("off") and 5 <= at.hour <= 11:
                    wakes.append((at, inst))
                    break
        wakes.sort(key=lambda x: x[0])
        if len(wakes) < 2 or (wakes[-1][0] - wakes[0][0]) > timedelta(minutes=90):
            return []
        slots, free = [], None
        for at, inst in wakes:
            start = max(at + timedelta(minutes=5), free or at)
            mins = avg.get(inst.id, length)
            slots.append((inst, start, start + timedelta(minutes=mins)))
            free = start + timedelta(minutes=mins + 2)
        return slots

    def _averages(self) -> dict[str, float]:
        life = getattr(self.home, "life", None)
        rows = life.watch._rows("shower", 30) if life else []
        out: dict[str, list] = {}
        for r in rows:
            if r.get("person"):
                out.setdefault(r["person"], []).append(r["minutes"])
        return {p: min(25.0, sum(v) / len(v)) for p, v in out.items()}

    def tick(self, now):
        at = clock.parse_hhmm(str(self.cfg.get("plan_at", "21:00")))
        key = now.strftime("%Y-%m-%d")
        d = self.load({})
        if d.get("planned") == key or (now.hour, now.minute) < (at.hour, at.minute):
            return
        d["planned"] = key
        slots = self.plan(now)
        d["slots"] = [{"person": i.id, "start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes")}
                      for i, s, e in slots]
        self.save(d)
        for inst, s, e in slots:
            self.tell([inst], "shower_slot", title="Tomorrow's shower", name=inst.name, start=clock.spoken_time(s),
                      end=clock.spoken_time(e))

    def heard(self, said, room, speaker):
        if re.match(r"^what(?:'s| is) my shower (?:slot|time)|^when (?:can|do) i shower", clean(said)) and speaker is not None:
            for sl in self.load({}).get("slots", []):
                if sl["person"] == speaker.id:
                    return "shower_slot", {"name": speaker.name, "start": clock.spoken_time(datetime.fromisoformat(sl["start"])),
                                           "end": clock.spoken_time(datetime.fromisoformat(sl["end"]))}
            return "shower_slot_none", {}
        return None


# ================================================================ what to wear
class Wear(Part):
    def advice(self, inst=None) -> str:
        w = getattr(self.home, "weather", None)
        d = (w.day(0) if w else {}) or {}
        cur = ((getattr(w, "data", None) or {}).get("current") or {}) if w else {}
        bits = []
        hi, lo = d.get("temperature_2m_max"), d.get("temperature_2m_min")
        f = bool(getattr(w, "f", False))
        if hi is not None:
            c_hi = (hi - 32) * 5 / 9 if f else hi
            c_lo = ((lo - 32) * 5 / 9 if f else lo) if lo is not None else c_hi
            if c_hi < 8:
                bits.append("a proper coat, it's cold all day")
            elif c_hi < 15:
                bits.append("a jacket or a warm jumper")
            elif c_hi < 22:
                bits.append("a light layer" + (", it's chilly early" if c_lo < 12 else ""))
            else:
                bits.append("something light, it's warm")
        rain = d.get("precipitation_probability_max")
        if rain is not None and rain >= 40:
            bits.append(f"a rain jacket or umbrella ({round(rain)} percent chance of rain)")
        if (d.get("uv_index_max") or 0) >= 6:
            bits.append("sunscreen and sunnies")
        if (d.get("wind_speed_10m_max") or 0) >= (25 if f else 40):
            bits.append("nothing that'll blow away")
        if inst is not None and inst.calendar:
            try:
                from .calendar_feed import upcoming

                today = " ".join(e["summary"].lower() for e in upcoming(inst.calendar, 1))
                if re.search(r"gym|training|footy|football|soccer|run|yoga|swim", today):
                    bits.append("pack your gym gear")
                if re.search(r"interview|presentation|formal|wedding|graduation|ceremony|dinner", today):
                    bits.append("something smart, you've got a " + re.search(
                        r"interview|presentation|formal|wedding|graduation|ceremony|dinner", today).group(0))
            except Exception:
                pass
        if not bits:
            return "I don't have the weather right now. Look outside, I guess."
        now_t = cur.get("temperature_2m")
        return (f"It's {round(now_t)} degrees now. " if now_t is not None else "") + "Wear " + join(bits) + "."

    def heard(self, said, room, speaker):
        if re.match(r"^what should i wear|^what do i wear|^do i need a (?:jacket|coat|jumper|umbrella)", clean(said)):
            self.say(self.advice(speaker if speaker is not None and not speaker.main else None), room=room)
            return "done"
        return None
