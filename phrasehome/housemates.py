"""Living together: the small stuff four people sharing a house argue about.

Noise meter      quiet hours (and exam nights): the shared rooms' mics only measure loudness; too loud for
                 too long and the house says "it's 11, keep it down". "how loud is it in here?"
Bathroom         "I'm next for the shower" queues you; when the bathroom's free (its sensor, the shower timer,
                 or "bathroom's free") the next person is told. A hall light shows free / busy.
Bin night        whoever's turn it is gets reminded until someone says "bins are out". Their turn done: done.
                 Someone else did it: +1 for them, -1 for whoever's turn it was.
Borrow log       "Finn borrowed my charger", "who has my charger?", "Finn gave my charger back".
House rules      house_rules in config, or "new house rule: shoes off inside". "are we allowed to...?"
                 checks the rules and your lease (upload it on /house): "what does the lease say about pets?"
Referee          "flip a coin", "roll a dice", "pick one of us", "settle this: ..." (the AI rules, briefly).
Locked out       "I'm locked out" (at the door, or "Finn locked out" from your phone): whoever's home is asked
                 to let you in; nobody home: everyone's asked who's closest. It never unlocks on a message.
Polls            "poll: pizza or tacos?" goes to everyone's phones; "I vote pizza" (or "vote pizza" from your
                 phone); the result's announced when everyone's voted or after 30 minutes.
Thermostat truce who keeps changing the temperature, and a number everyone can live with: "thermostat truce".
House calendar   "the inspection's on Friday at 10", "Jo's got a guest staying Saturday", "what's on this week?".
                 Book the TV or the lounge: "I've got the TV Saturday at 7", "who's got the TV tonight?".
                 Subscribe on your phone: /house.ics
Errands          "anyone going to the shops?", "I'm going to the shops", "get me milk": the runner gets the list.
Move-in list     "I'm bringing a kettle", "who's bringing a toaster?", "what do we still need?"
Birthday stealth "secret from Parker: surprise dinner on Saturday": never said in Parker's room or to Parker,
                 never given to the AI when Parker asks; a week before, the others get the plans on their phones.

noise: { quiet_hours: "23:00-07:00", level: -30, seconds: 20 }     # level in dBFS: "how loud is it" helps set it
bathroom: { sensor: { node: bathroom, input: door, busy_when: off }, light: { node: hall, channel: bathroom_led } }
bins: { night: thu, time: "19:00", until: "23:00", rota: [finn, parker, alex, jo] }
house_rules: [shoes off inside, quiet after 11 on weeknights, label your food]
thermostat: { set: [ { node: hall, channel: thermostat, level: "{temp}" } ] }
move_in: { items: [kettle, toaster, microwave, vacuum] }
"""

from __future__ import annotations

import logging
import math
import random
import re
import time
import uuid
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

from . import clock
from .life_base import Part, clean, join

log = logging.getLogger("phrasehome.housemates")

MOVE_IN = ["kettle", "toaster", "microwave", "vacuum", "pots and pans", "frying pan", "cutlery", "plates", "bowls",
           "mugs", "glasses", "chopping board", "kitchen knives", "baking tray", "dish rack", "iron", "laundry rack",
           "kitchen bin", "broom and mop", "extension leads", "wifi router", "tv", "first aid kit", "toolkit",
           "shower curtain", "bath mat", "toilet brush", "plunger", "tea towels", "cleaning spray"]


def _when(at: datetime) -> str:
    today = clock.now().date()
    day = "today" if at.date() == today else "tomorrow" if at.date() == today + timedelta(days=1) else at.strftime("%A")
    return f"{day} at {clock.spoken_time(at)}"


# ================================================================ noise meter
class Noise(Part):
    section = "noise"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.window = str(self.cfg.get("quiet_hours", "23:00-07:00"))
        self.limit = float(self.cfg.get("level", -30))
        self.seconds = float(self.cfg.get("seconds", 20))
        self.rooms = [str(r).lower() for r in (self.cfg.get("rooms") or home.instances.main.rooms or [])]
        self.levels: dict[str, deque] = {}
        self.now_db: dict[str, float] = {}
        self._warned: dict[str, float] = {}

    def quiet(self) -> bool:
        life = getattr(self.home, "life", None)
        if life and (life.exams.quiet_now() or (life.party.active and life.party.active.get("quieted"))):
            return True
        return clock.in_window(self.window, clock.now())

    def level(self, room: str | None, pcm: bytes) -> float | None:
        if not room or len(pcm) < 400:
            return None
        import numpy as np

        x = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        db = 20 * math.log10(max(rms, 1.0) / 32768.0)
        room = room.lower()
        self.now_db[room] = db
        q = self.levels.setdefault(room, deque(maxlen=600))
        now = time.time()
        q.append((now, db))
        if room in self.rooms and not self.home.state.is_private(room) and self.quiet():
            recent = [d for t, d in q if now - t <= self.seconds]
            span = now - q[0][0] if q else 0
            if span >= self.seconds * 0.9 and recent and sum(recent) / len(recent) > self.limit \
                    and now - self._warned.get(room, 0) > 600:
                self._warned[room] = now
                self.home.events.log("noise.loud", room=room, db=round(sum(recent) / len(recent)))
                self.say("noise_warn", room=room, time=clock.spoken_time(clock.now()))
        return db

    def heard(self, said, room, speaker):
        if re.match(r"^how loud is it(?: in here)?$|^noise level$|^how noisy is it$", clean(said)):
            db = self.now_db.get((room or "").lower())
            if db is None:
                return "noise_unknown", {}
            return "noise_level", {"db": round(db), "limit": round(self.limit)}
        return None


# ================================================================ bathroom queue + free light
class Bathroom(Part):
    section = "bathroom"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.queue: list[str] = []
        self._busy_was: bool | None = None

    def busy(self) -> bool | None:
        s = self.cfg.get("sensor") or {}
        life = getattr(self.home, "life", None)
        if life and life.watch.shower:
            return True
        if s and self.home.sensors:
            cur = self.home.sensors.current(s.get("node"), s.get("input"))
            if cur is not None:
                return cur.get("state") == str(s.get("busy_when", "off")).lower()
        return None

    def _light(self, busy: bool) -> None:
        lt = self.cfg.get("light")
        if not lt:
            return
        if "busy" in lt or "free" in lt:
            steps = lt.get("busy" if busy else "free") or []
            self.home.run_steps(steps if isinstance(steps, list) else [steps], {}, "bathroom light")
        else:
            a = {**lt, "state": busy}
            a.setdefault("backend", self.home.config.settings.default_backend)
            self.home.run_action(a)

    def freed(self) -> None:
        self._light(False)
        if not self.queue:
            return
        pid = self.queue.pop(0)
        inst = self.home.instances.all.get(pid)
        if inst:
            self.say("bathroom_your_turn", room=self.room_of(inst), name=inst.name)
            self.tell([inst], "bathroom_your_turn", title="Bathroom's free", name=inst.name)

    def on_event(self, ev):
        s = self.cfg.get("sensor") or {}
        if ev["kind"] == "sensor" and s and ev.get("node") == str(s.get("node", "")).lower() \
                and ev.get("input") == str(s.get("input", "")).lower() and "state" in ev:
            busy = ev["state"] == str(s.get("busy_when", "off")).lower()
            if busy != self._busy_was:
                self._busy_was = busy
                self._light(busy) if busy else self.freed()
        elif ev["kind"] == "shower.off" and not (self.cfg.get("sensor")):
            self.freed()

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        if re.match(r"^(?:i'm|i am) next (?:for|in) the (?:shower|bathroom)|^(?:put|add) me (?:in|on|to) the (?:shower|bathroom) queue|"
                    r"^can i (?:have|get) the (?:shower|bathroom) next|^(?:shower|bathroom) queue me$", s):
            if me is None:
                return None
            if me.id not in self.queue:
                self.queue.append(me.id)
            n = self.queue.index(me.id) + 1
            return "bathroom_queued", {"name": me.name, "place": {1: "next", 2: "second", 3: "third"}.get(n, f"number {n}")}
        if re.match(r"^take me (?:off|out of) the (?:shower|bathroom) queue|^i don't need the (?:shower|bathroom)", s):
            if me and me.id in self.queue:
                self.queue.remove(me.id)
            return "bathroom_dequeued", {"name": getattr(me, "name", "")}
        if re.match(r"^who's (?:in|on) the (?:shower|bathroom) queue|^(?:shower|bathroom) queue$|^who's next for the (?:shower|bathroom)", s):
            return ("bathroom_queue", {"names": join(self.name(p) for p in self.queue)}) if self.queue else ("bathroom_queue_empty", {})
        if re.match(r"^(?:the )?bathroom(?:'s| is) (?:free|empty)|^i'm out of the bathroom$", s):
            self.freed()
            return "done"
        if re.match(r"^is (?:the )?(?:bathroom|shower|toilet|loo) (?:free|empty|busy|in use)", s):
            b = self.busy()
            return "bathroom_busy" if b else "bathroom_free" if b is False else "bathroom_unknown", \
                {"names": join(self.name(p) for p in self.queue)}
        return None


# ================================================================ bin night
class Bins(Part):
    section = "bins"

    def rota(self) -> list:
        r = [self.find(p) for p in self.cfg.get("rota") or []]
        return [i for i in r if i] or self.housemates()

    def turn(self, now=None):
        now = now or clock.now()
        r = self.rota()
        d = self.load({})
        n = int(d.get("offset", 0)) + now.isocalendar()[1]
        return r[n % len(r)]

    def tick(self, now):
        if not self.cfg:
            return
        night = str(self.cfg.get("night", "thu"))
        if not clock.day_matches(night, now.date()):
            return
        t = clock.parse_hhmm(str(self.cfg.get("time", "19:00")))
        until = clock.parse_hhmm(str(self.cfg.get("until", "23:00")))
        key = now.strftime("%Y-%m-%d")
        d = self.load({})
        st = d.setdefault("nights", {}).setdefault(key, {})
        if st.get("done") or (now.hour, now.minute) < (t.hour, t.minute):
            return
        inst = self.turn(now)
        points = self.home.life.points
        if not st.get("job"):
            due = now.replace(hour=until.hour, minute=until.minute, second=0, microsecond=0)
            st["job"] = points.give_job(inst, "take the bins out", due, kind="bins")["id"]
            st["person"], st["next"] = inst.id, 0
        if time.time() >= st.get("next", 0):
            st["next"] = time.time() + float(self.cfg.get("every", 30)) * 60
            self.say("bins_reminder", room=self.room_of(inst), name=inst.name)
            self.tell([inst], "bins_reminder", title="Bin night", name=inst.name)
        self.save(d)

    def heard(self, said, room, speaker):
        s = clean(said)
        if not re.match(r"^(?:the )?bins(?:'re| are)? (?:out|done)|^(?:i )?(?:took|put|taken|have taken|'ve taken) (?:the )?bins? out|"
                        r"^bins? done$|^done the bins$", s):
            if re.match(r"^whose turn is it (?:for|to do|on) (?:the )?bins|^who's (?:on|doing) (?:the )?bins", s):
                return "bins_turn", {"name": self.turn().name}
            return None
        key = clock.now().strftime("%Y-%m-%d")
        d = self.load({})
        st = d.setdefault("nights", {}).setdefault(key, {})
        holder = self.home.instances.all.get(st.get("person")) or self.turn()
        me = speaker if speaker is not None and not speaker.main else None
        points = self.home.life.points
        st["done"] = True
        self.save(d)
        if me is None or me.id == holder.id:
            points.finish_job(holder, "take the bins out")
            return "bins_done", {"name": holder.name}
        points.add(me.id, 1, "took the bins out for " + holder.name, me.id, willing=True)
        points.finish_job(holder, "take the bins out")
        points.bad(holder, f"bin night: {me.name} did it")
        self.tell([holder], "bins_someone_else", title="Bin night", name=holder.name, helper=me.name)
        return "bins_helper", {"name": me.name, "holder": holder.name}


# ================================================================ borrow log
class Borrow(Part):
    store = "borrow"

    def heard(self, said, room, speaker):
        s = clean(said)
        who = self.who_rx()
        me = speaker if speaker is not None and not speaker.main else None
        pats = [(rf"^({who}) (?:borrowed|took|has|nicked|pinched) my (.+)$", "them_mine"),
                (rf"^i (?:borrowed|took|have|nicked) ({who})'s (.+)$", "me_theirs"),
                (rf"^i lent ({who}) my (.+)$", "them_mine")]
        for p, kind in pats:
            m = re.match(p, s)
            if m and me is not None:
                other = self.find(m.group(1))
                item = re.sub(r"\s+(?:again|today|yesterday)$", "", m.group(2))
                owner, holder = (me, other) if kind == "them_mine" else (other, me)
                d = self.load({"loans": []})
                d["loans"].append({"id": uuid.uuid4().hex[:6], "item": item, "owner": owner.id, "holder": holder.id,
                                   "since": clock.now().strftime("%Y-%m-%d"), "back": False})
                self.save(d)
                return "borrow_saved", {"holder": holder.name, "owner": owner.name, "item": item}
        m = re.match(rf"^(?:who has|who's got|who borrowed|where's|where is) my (.+)$", s)
        if m and me is not None:
            loan = self._open(owner=me.id, item=m.group(1))
            if loan:
                return "borrow_who", {"holder": self.name(loan["holder"]), "item": loan["item"], "since": _since(loan["since"])}
            return None  # not lent out: maybe "where I put things" knows
        m = re.match(rf"^({who}) (?:gave|brought|handed) (?:me )?(?:back )?my (.+?)(?: back)?$", s) or \
            re.match(rf"^i (?:gave|got) (?:({who})'s|my) (.+?) back$", s)
        if m and me is not None:
            item = m.group(2)
            loan = self._open(item=item, holder=me.id) or self._open(owner=me.id, item=item)
            if loan:
                d = self.load({"loans": []})
                for x in d["loans"]:
                    if x["id"] == loan["id"]:
                        x["back"] = True
                self.save(d)
                return "borrow_back", {"item": loan["item"], "owner": self.name(loan["owner"])}
        if re.match(r"^what (?:have i|did i) (?:borrowed|borrow|lent out|lend)", s) and me is not None:
            mine = [x for x in self.load({"loans": []})["loans"] if not x["back"] and me.id in (x["owner"], x["holder"])]
            if not mine:
                return "borrow_none", {}
            return "borrow_list", {"items": join(f"{self.name(x['holder'])} has {self.name(x['owner'])}'s {x['item']}" for x in mine)}
        return None

    def _open(self, owner=None, item=None, holder=None):
        words = set((item or "").split()) - {"the", "a", "my"}
        for x in reversed(self.load({"loans": []})["loans"]):
            if x["back"] or (owner and x["owner"] != owner) or (holder and x["holder"] != holder):
                continue
            if not words or words & set(x["item"].split()):
                return x
        return None


def _since(d: str) -> str:
    day = datetime.strptime(d, "%Y-%m-%d").date()
    n = (clock.now().date() - day).days
    return "today" if n == 0 else "yesterday" if n == 1 else day.strftime("%A") if n < 7 else f"{n} days ago"


# ================================================================ house rules + the lease
class Rules(Part):
    store = "rules"

    def rules(self) -> list[str]:
        base = [str(r) for r in self.home.config.extra.get("house_rules") or []]
        return base + [r for r in self.load({"rules": []})["rules"] if r not in base]

    def lease_path(self) -> Path:
        return self.path.with_name("lease.txt")

    def lease(self) -> str:
        try:
            return self.lease_path().read_text()
        except OSError:
            return ""

    def save_lease(self, data: bytes, filename: str = "") -> int:
        if filename.lower().endswith(".pdf") or data[:4] == b"%PDF":
            text = _pdf_text(data)
        else:
            text = data.decode("utf-8", "replace")
        self.lease_path().parent.mkdir(parents=True, exist_ok=True)
        self.lease_path().write_text(text)
        return len(text.split())

    def search(self, question: str, n: int = 4) -> list[str]:
        words = {w for w in re.findall(r"[a-z]{3,}", question.lower())} - {"can", "are", "allowed", "the", "what",
                                                                          "does", "lease", "say", "about", "our", "we"}
        chunks = [c.strip() for c in re.split(r"\n\s*\n|(?<=\.)\s+(?=\d+\.)", self.lease()) if len(c.strip()) > 30]
        scored = sorted(((sum(w in c.lower() for w in words), c) for c in chunks), key=lambda x: -x[0])
        return [c[:700] for s, c in scored[:n] if s]

    def heard(self, said, room, speaker):
        s = clean(said)
        m = re.match(r"^(?:add a |new )?house rule:? (.+)$|^add (?:a )?house rule (?:that )?(.+)$", s)
        if m:
            rule = (m.group(1) or m.group(2)).strip()
            d = self.load({"rules": []})
            if rule not in d["rules"]:
                d["rules"].append(rule)
                self.save(d)
            return "rule_added", {"rule": rule}
        m = re.match(r"^(?:remove|delete|scrap) the (?:house )?rule (?:about )?(.+)$", s)
        if m:
            d = self.load({"rules": []})
            d["rules"] = [r for r in d["rules"] if m.group(1) not in r.lower()]
            self.save(d)
            return "rule_removed", {}
        if re.match(r"^what are the (?:house )?rules|^(?:read|tell me) the (?:house )?rules", s):
            rules = self.rules()
            return ("rules_list", {"rules": join(rules)}) if rules else ("rules_none", {})
        if re.match(r"^(?:are we|am i|is (?:it|anyone)) allowed to |^can we (?:have|keep|paint|put|get|sublet|smoke|hang|drill)|"
                    r"^what does (?:the|our) lease say|^does (?:the|our) lease", s):
            return self.answer(said, room)
        return None

    def answer(self, question: str, room=None):
        rules, lease = self.rules(), self.search(question)
        if not rules and not lease:
            return "rules_nothing", {}
        a = getattr(self.home, "assistant", None)
        if a is not None:
            try:
                from . import ai

                prompt = (f"House rules: {rules or 'none'}.\nRelevant parts of the lease: {lease or 'none found'}.\n"
                          f"Question from a housemate: {question}\nAnswer in two short spoken sentences, saying whether "
                          "it's a house rule or the lease, and quote the clause briefly if it's the lease. If neither "
                          "covers it, say so. Not legal advice.")
                text = ai.ask(self.home, prompt, quick=True, max_tokens=500)
                if isinstance(text, str) and text.strip():
                    self.say(text.strip(), room=room)
                    return "done"
            except Exception:
                pass
        if lease:
            return "lease_says", {"text": lease[0][:300]}
        return "rules_list", {"rules": join(rules)}


def _pdf_text(data: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        return "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except ImportError:
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("pdftotext"):
            raise RuntimeError("to read PDFs: pip install pypdf (or install poppler's pdftotext)")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            return subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, timeout=60).stdout


# ================================================================ referee
class Referee(Part):
    def heard(self, said, room, speaker):
        s = clean(said)
        if re.match(r"^(?:flip|toss) a coin$|^heads or tails$", s):
            return "coin", {"side": random.choice(["heads", "tails"])}
        m = re.match(r"^roll (?:a |the |two )?(?:dice|die)$", s)
        if m:
            n = 2 if "two" in s else 1
            rolls = [random.randint(1, 6) for _ in range(n)]
            return "dice", {"roll": join(str(r) for r in rolls)}
        m = re.match(r"^pick a number (?:between|from) (\d+) (?:and|to) (\d+)$", s)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            return "pick_number", {"number": random.randint(lo, hi)}
        if re.match(r"^(?:pick|choose) (?:one of us|someone|a person|a housemate)|^who should (?:go|do it|pay|cook|clean)", s):
            home_now = [self.find(p) for p in self.home.state.home_people()]
            pool = [i for i in home_now if i and not i.main] or self.housemates()
            return "pick_person", {"name": random.choice(pool).name}
        m = re.match(r"^(?:settle this|settle an argument|referee this|who's right|who is right)[:,]? ?(.*)$", s)
        if m:
            a = getattr(self.home, "assistant", None)
            if a is None or not m.group(1):
                return "coin", {"side": random.choice(["heads", "tails"])}
            try:
                answer = a.ask("You're the house referee. Settle this argument between housemates with a clear "
                               "ruling in two short, funny but fair sentences; check facts with search if you need "
                               "to: " + m.group(1), room=room, person=getattr(speaker, "id", None))
            except Exception as e:  # the AI isn't available: a coin's fair too
                log.warning("referee: the AI couldn't answer: %s", e)
                return "coin", {"side": random.choice(["heads", "tails"])}
            self.say(answer, room=room)
            return "done"
        return None


# ================================================================ locked out
class LockedOut(Part):
    store = "locked_out"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.active: dict[str, dict] = {}

    def start(self, inst, door: str = "front") -> str:
        h = self.home
        here = [self.find(p) for p in h.state.home_people()]
        here = [i for i in here if i and not i.main and i.id != inst.id]
        self.active[inst.id] = {"t": time.time(), "coming": None}
        h.events.log("locked_out", person=inst.id)
        if here:
            self.tell(here, "locked_out_ask", title="Locked out", name=inst.name, door=door)
            h.voice.speak("locked_out_ask", rooms=[r for i in here for r in i.rooms[:1]] + (self.shared_rooms() or []),
                          name=inst.name, door=door)
            self.tell([inst], "locked_out_told", title="Locked out", names=join(i.name for i in here))
            return "asked " + join(i.name for i in here)
        self.tell(self.others(inst), "locked_out_closest", title="Locked out", name=inst.name)
        self.tell([inst], "locked_out_nobody", title="Locked out")
        return "nobody home; asked who's closest"

    def coming(self, helper, minutes: float | None = None) -> str | None:
        for pid, st in self.active.items():
            if pid == helper.id or st.get("coming"):
                continue
            st["coming"] = helper.id
            inst = self.home.instances.all.get(pid)
            self.tell([inst], "locked_out_coming", title="Locked out", name=helper.name,
                      minutes=f" in about {int(minutes)} minutes" if minutes else "")
            return pid
        return None

    def on_event(self, ev):
        if ev["kind"] in ("presence.arrived", "door.unlocked", "scan") and self.active:
            pid = ev.get("person")
            if pid in self.active:
                st = self.active.pop(pid)
                if st.get("coming"):
                    self.home.life.points.add(st["coming"], 1, f"let {self.name(pid)} in", st["coming"], willing=True)
            elif ev["kind"] == "door.unlocked":
                for p, st in list(self.active.items()):
                    if st.get("coming"):
                        self.home.life.points.add(st["coming"], 1, f"let {self.name(p)} in", st["coming"], willing=True)
                    self.active.pop(p, None)

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        if re.match(r"^(?:i'm|i am|i got|i've got|i have been) locked out|^locked out$|^let me in$", s) and me is not None:
            self.start(me)
            return "locked_out_ok", {"name": me.name}
        m = re.match(rf"^(?:i'm coming|i'll let (?:them|him|her|({self.who_rx()})) in|coming|on my way|i'm on my way)(?:,? (?:in )?(\d+)(?: minutes?| mins?)?)?$", s) \
            or re.match(r"^(?:i'm )?(\d+) (?:minutes?|mins?) away$", s)
        if m and me is not None and self.active:
            mins = next((float(g) for g in m.groups() if g and g.isdigit()), None)
            if self.coming(me, mins):
                return "locked_out_thanks", {"name": me.name}
        return None


# ================================================================ polls
class Polls(Part):
    store = "polls"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.poll: dict | None = None

    def start(self, question: str, options: list[str], by=None, minutes: float = 30) -> str:
        self.poll = {"question": question, "options": options, "votes": {}, "by": getattr(by, "id", ""),
                     "until": time.time() + minutes * 60}
        self.home.events.log("poll.started", question=question)
        self.tell(self.housemates(), "poll_started", title="House poll", name=getattr(by, "name", "Someone"),
                  question=question, options=join(options).replace(" and ", " or "))
        return "poll started"

    def vote(self, inst, choice: str) -> str | None:
        p = self.poll
        if not p or inst is None:
            return None
        c = choice.strip().lower()
        opt = next((o for o in p["options"] if o.lower() == c), None) or \
            next((o for o in p["options"] if c in o.lower() or o.lower() in c), None)
        if opt is None:
            return None
        p["votes"][inst.id] = opt
        if all(i.id in p["votes"] for i in self.housemates()):
            self.finish()
        return opt

    def finish(self) -> str:
        p, self.poll = self.poll, None
        if not p:
            return "no poll"
        counts = {o: list(p["votes"].values()).count(o) for o in p["options"]}
        best = max(counts.values()) if counts else 0
        winners = [o for o, n in counts.items() if n == best and n]
        if not winners:
            result = "nobody voted"
        elif len(winners) > 1:
            result = f"a tie between {join(winners)}. I pick {random.choice(winners)}"
        else:
            result = f"{winners[0]}, with {best} vote{'s' if best != 1 else ''}"
        self.home.events.log("poll.result", question=p["question"], result=result)
        self.home.voice.speak("poll_result", rooms=self.shared_rooms(), question=p["question"], result=result)
        self.tell(self.housemates(), "poll_result", title="Poll result", question=p["question"], result=result)
        return result

    def tick(self, now):
        if self.poll and time.time() >= self.poll["until"]:
            self.finish()

    def heard(self, said, room, speaker):
        s = clean(said)
        m = re.match(r"^(?:start a |make a |run a )?(?:poll|vote)[:,]? (.+)$", s)
        if m and " or " in m.group(1):
            q = m.group(1).rstrip("?")
            opts = [o.strip() for o in re.split(r",| or ", q) if o.strip()]
            if 2 <= len(opts) <= 6:
                self.start(q + "?", opts, speaker)
                return "poll_ok", {"options": join(opts).replace(" and ", " or ")}
        m = re.match(r"^(?:i vote(?: for)?|vote(?: for)?|my vote is|put me down for) (.+)$", s)
        if m and self.poll:
            got = self.vote(speaker, m.group(1))
            if got:
                return "poll_voted", {"choice": got}
        if re.match(r"^(?:end|close|finish) the poll|^poll results?$", s) and self.poll:
            self.finish()
            return "done"
        return None


# ================================================================ thermostat truce
class Thermostat(Part):
    section = "thermostat"

    def prefs(self) -> dict[str, float]:
        return {k: float(v) for k, v in self.load({}).get("prefs", {}).items()}

    def note(self, pid: str, temp: float, changed: bool = True) -> None:
        d = self.load({})
        d.setdefault("prefs", {})[pid] = temp
        if changed:
            d.setdefault("changes", []).append({"person": pid, "temp": temp, "t": time.time()})
            d["changes"] = d["changes"][-100:]
        self.save(d)
        if changed:
            recent = [c for c in d["changes"] if time.time() - c["t"] < 6 * 3600]
            if len(recent) >= 3 and len({c["person"] for c in recent}) >= 2 and time.time() - d.get("war", 0) > 86400:
                d["war"] = time.time()
                self.save(d)
                self.home.voice.speak("thermo_war", rooms=self.shared_rooms(), **self._truce_vals())

    def truce(self) -> float | None:
        p = sorted(self.prefs().values())
        if not p:
            return None
        mid = len(p) // 2
        return p[mid] if len(p) % 2 else (p[mid - 1] + p[mid]) / 2

    def _truce_vals(self) -> dict:
        t = self.truce()
        who = join(f"{self.name(k)} likes {v:g}" for k, v in self.prefs().items())
        return {"who": who, "temp": f"{round(t * 2) / 2:g}" if t is not None else "?"}

    def on_event(self, ev):
        if ev["kind"] != "command" or not ev.get("person"):
            return
        heard = str(ev.get("heard") or "")
        if re.search(r"\b(temp|temperature|heat|heating|thermostat|ac|aircon|air con|degrees)\b", heard):
            m = re.search(r"\b(\d{2}(?:\.\d)?)\b", heard)
            if m and 10 <= float(m.group(1)) <= 90:
                self.note(str(ev["person"]), float(m.group(1)))

    def heard(self, said, room, speaker):
        s = clean(said)
        m = re.match(r"^i (?:like|prefer|want) it (?:at |around )?(\d{2}(?:\.\d)?)(?: degrees)?$|^i'm (?:happy|comfy|comfortable) at (\d{2}(?:\.\d)?)", s)
        if m and speaker is not None and not speaker.main:
            self.note(speaker.id, float(m.group(1) or m.group(2)), changed=False)
            return "thermo_pref", {"name": speaker.name, "temp": m.group(1) or m.group(2)}
        if re.match(r"^(?:thermostat truce|temperature truce|what temperature should we (?:agree on|set|use)|truce)$", s):
            if self.truce() is None:
                return "thermo_none", {}
            return "thermo_truce", self._truce_vals()
        if re.match(r"^(?:set|use|apply) the truce(?: temperature)?$", s) and self.cfg.get("set") and self.truce():
            self.home.run_steps(self.cfg["set"], {"temp": str(round(self.truce()))}, "thermostat truce")
            return "thermo_set", self._truce_vals()
        return None


# ================================================================ house calendar + bookings
RESOURCES = {"tv": "TV", "telly": "TV", "television": "TV", "lounge": "lounge", "living room": "lounge",
             "kitchen": "kitchen", "car": "car", "washing machine": "washing machine", "bathroom": "bathroom"}
KINDS = [("inspection", r"inspection|landlord|real estate|agent"), ("cleaners", r"cleaner"),
         ("guest", r"guest|staying over|stay over|visiting|visit|parents"), ("party", r"party|pres|predrinks|birthday"),
         ("repair", r"plumber|electrician|repair|fix|maintenance")]


class HouseCalendar(Part):
    store = "calendar"

    def events(self, days: float = 14, include_past_hours: float = 3) -> list[dict]:
        now = clock.now()
        out = []
        for e in self.load({"events": []})["events"]:
            at = datetime.fromisoformat(e["start"])
            if now - timedelta(hours=include_past_hours) <= at <= now + timedelta(days=days):
                out.append(e)
        return sorted(out, key=lambda e: e["start"])

    def add(self, what: str, start: datetime, end: datetime | None = None, by=None, resource: str = "",
            kind: str = "", secret_from: str = "") -> dict:
        kind = kind or next((k for k, rx in KINDS if re.search(rx, what)), "event")
        e = {"id": uuid.uuid4().hex[:6], "what": what.strip(), "start": start.isoformat(timespec="minutes"),
             "end": (end or start + timedelta(hours=2)).isoformat(timespec="minutes"), "by": getattr(by, "id", by) or "",
             "resource": resource, "kind": kind, "secret_from": secret_from, "told": []}
        d = self.load({"events": []})
        d["events"] = [x for x in d["events"] if datetime.fromisoformat(x["end"]) > clock.now() - timedelta(days=60)]
        d["events"].append(e)
        self.save(d)
        self.home.events.log("calendar.added", what=e["what"], start=e["start"])
        return e

    def clash(self, resource: str, start: datetime, end: datetime) -> dict | None:
        for e in self.events(30):
            if e["resource"] == resource and datetime.fromisoformat(e["start"]) < end and start < datetime.fromisoformat(e["end"]):
                return e
        return None

    def ics(self) -> str:
        def fmt(s):
            return datetime.fromisoformat(s).astimezone().strftime("%Y%m%dT%H%M%S")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//phrasehome//house//EN", "X-WR-CALNAME:House"]
        for e in self.events(60, 24 * 7):
            if e.get("secret_from"):
                continue
            title = f"{e['what']}" + (f" ({self.name(e['by'])})" if e["resource"] and e["by"] else "")
            lines += ["BEGIN:VEVENT", f"UID:{e['id']}@phrasehome", f"DTSTART:{fmt(e['start'])}", f"DTEND:{fmt(e['end'])}",
                      f"SUMMARY:{title}", "END:VEVENT"]
        return "\r\n".join(lines + ["END:VCALENDAR"]) + "\r\n"

    def tick(self, now):
        d = self.load({"events": []})
        changed = False
        for e in d["events"]:
            at = datetime.fromisoformat(e["start"])
            left = (at - now).total_seconds() / 60
            if e["kind"] in ("inspection", "cleaners", "guest", "repair") and 0 < left <= 24 * 60 and \
                    now.hour >= 19 and "evening" not in e["told"]:
                e["told"].append("evening")
                changed = True
                people = [i for i in self.housemates() if i.id != e.get("secret_from")]
                self.tell(people, "calendar_tomorrow", title="House calendar", what=e["what"], when=_when(at))
            if e["resource"] and 0 < left <= 30 and "soon" not in e["told"]:
                e["told"].append("soon")
                changed = True
                owner = self.home.instances.all.get(e["by"])
                if owner:
                    self.tell([owner], "booking_soon", title="Your booking", what=e["resource"], when=clock.spoken_time(at))
        if changed:
            self.save(d)

    def on_event(self, ev):
        if ev["kind"] == "party.on":
            self.add("party", clock.now(), clock.now() + timedelta(hours=5), kind="party")

    def heard(self, said, room, speaker):
        s = clean(said)
        from .study import parse_when

        m = re.match(r"^(?:i've got|i have|i'm booking|book|i'm taking|i'll have|can i have|i want) (?:the )?"
                     r"(tv|telly|television|lounge|living room|car|washing machine)(?: room)? (.+?)(?: for (.+))?$", s)
        if m and speaker is not None and not speaker.main:
            start = parse_when(" " + m.group(2), mode="evening")
            if start is None:
                return None
            end = None
            mt = re.search(r"\b(?:till|until|to) (\d{1,2})(?::(\d{2}))?\s*(am|pm)?", m.group(2))
            if mt:
                h = int(mt.group(1)) + (12 if (mt.group(3) == "pm" or (not mt.group(3) and int(mt.group(1)) < 8)) and int(mt.group(1)) < 12 else 0)
                end = start.replace(hour=h % 24, minute=int(mt.group(2) or 0))
                if end <= start:
                    end += timedelta(days=1)
            end = end or start + timedelta(hours=3)
            res = RESOURCES[m.group(1)]
            hit = self.clash(res, start, end)
            if hit and hit["by"] != speaker.id:
                return "booking_clash", {"name": self.name(hit["by"]), "what": res, "when": _when(datetime.fromisoformat(hit["start"]))}
            self.add(m.group(3) or f"{speaker.name} has the {res}", start, end, speaker, resource=res, kind="booking")
            return "booking_ok", {"name": speaker.name, "what": res, "when": _when(start)}
        m = re.match(r"^who(?:'s| has) got the (tv|telly|television|lounge|living room|car)(?: (.+))?$|^who has the (tv|lounge|car)(?: (.+))?$", s)
        if m:
            res = RESOURCES[m.group(1) or m.group(3)]
            now = clock.now()
            for e in self.events(2):
                if e["resource"] == res and datetime.fromisoformat(e["end"]) > now:
                    return "booking_who", {"name": self.name(e["by"]), "what": res, "when": _when(datetime.fromisoformat(e["start"]))}
            return "booking_free", {"what": res}
        if re.match(r"^what's on (?:this week|this weekend|the house calendar|in the house)|^(?:house calendar|anything on this (?:week|weekend))$", s):
            evs = [e for e in self.events(7) if not (speaker is not None and e.get("secret_from") == speaker.id)]
            if not evs:
                return "calendar_empty", {}
            return "calendar_list", {"items": join(f"{e['what']} {_when(datetime.fromisoformat(e['start']))}" for e in evs[:6])}
        m = re.match(r"^(?:add to the house calendar|put on the house calendar|house calendar)[:,]? (.+)$", s) or \
            re.match(r"^(?:the )?((?:inspection|cleaners?|plumber|electrician|landlord)\b.*?)(?:'s| is| are| come| coming)? (?:on |at |coming )?"
                     r"((?:today|tomorrow|tonight|mon|tue|wed|thu|fri|sat|sun|next).*)$", s) or \
            re.match(rf"^(({self.who_rx()})(?:'s got| has) (?:a |some )?(?:guests?|people|friends?|parents?|family) (?:staying|coming|over|visiting).*?)"
                     r"((?:today|tomorrow|tonight|this weekend|on \w+day|\w+day|next \w+).*)$", s)
        if m:
            text = m.group(1)
            tail = m.group(m.lastindex) if m.lastindex and m.lastindex >= 2 else text
            start = parse_when(" " + tail) or parse_when(" " + text)
            if start is None:
                return None
            what = re.sub(r"\s+(?:on|at)\s+(?:today|tomorrow|tonight|mon|tue|wed|thu|fri|sat|sun)\w*.*$", "", text).strip()
            e = self.add(what, start, by=speaker)
            return "calendar_added", {"what": e["what"], "when": _when(start)}
        return None


# ================================================================ errands
class Errands(Part):
    store = "errands"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.run: dict | None = None

    def going(self, inst, where: str = "the shops", minutes: float = 30) -> str:
        self.run = {"by": inst.id, "where": where, "list": [], "for": [], "until": time.time() + minutes * 60}
        self.tell(self.others(inst), "errand_going", title="Errand", name=inst.name, where=where)
        return "told everyone"

    def ask(self, inst, item: str) -> str | None:
        r = self.run
        if not r or inst is None or inst.id == r["by"]:
            return None
        r["list"].append(f"{item} for {inst.name}")
        if inst.id not in r["for"]:
            r["for"].append(inst.id)
        runner = self.home.instances.all.get(r["by"])
        self.tell([runner], "errand_request", title="Errand", name=inst.name, item=item)
        return runner.name if runner else None

    def back(self, inst) -> str | None:
        r = self.run
        if not r or inst is None or inst.id != r["by"]:
            return None
        self.run = None
        if r["for"]:
            self.home.life.points.add(inst.id, 1, f"ran errands for {join(self.name(p) for p in r['for'])}", inst.id, willing=True)
            self.tell([self.home.instances.all.get(p) for p in r["for"]], "errand_back", title="Errand", name=inst.name)
        return inst.name

    def tick(self, now):
        if self.run and time.time() > self.run["until"] + 3 * 3600:
            self.run = None

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        if re.match(r"^(?:is )?any(?:one|body) going (?:to )?(?:the )?(shops?|supermarket|store|servo|bottle ?o|uni|campus)", s):
            where = re.search(r"(shops?|supermarket|store|servo|bottle ?o|uni|campus)", s).group(1)
            self.tell(self.others(me), "errand_anyone", title="Errand", name=getattr(me, "name", "Someone"), where=f"the {where}")
            return "errand_asked", {"where": f"the {where}"}
        m = re.match(r"^(?:i'm|i am) (?:going|heading|popping|off) (?:to |down to |out to )?(?:the )?(shops?|supermarket|store|servo|bottle ?o)", s)
        if m and me is not None:
            self.going(me, f"the {m.group(1)}")
            return "errand_ok", {"where": f"the {m.group(1)}"}
        m = re.match(r"^(?:can you |could you |ask \w+ to )?(?:get|grab|pick up) me (?:some |a |an )?(.+?)(?: (?:from|at) the \w+)?(?: please)?$", s) or \
            re.match(r"^i need (?:some |a |an )?(.+?) from the (?:shops?|supermarket|store)$", s)
        if m and self.run and me is not None:
            runner = self.ask(me, m.group(1))
            if runner:
                return "errand_added", {"item": m.group(1), "name": runner}
        if re.match(r"^(?:i'm |i am )?back from the (?:shops?|supermarket|store)|^got everything$|^i'm back with the shopping$", s) and me is not None:
            if self.back(me):
                return "errand_done", {"name": me.name}
        return None


# ================================================================ move-in list
class MoveIn(Part):
    section = "move_in"

    def items(self) -> list[str]:
        return [str(i).lower() for i in (self.cfg.get("items") or MOVE_IN)]

    def heard(self, said, room, speaker):
        s = clean(said)
        claims = self.load({}).get("claims", {})
        m = re.match(r"^(?:i'm|i am|i'll be|i will be|i can) (?:bringing|bring) (?:a |an |my |the |some )?(.+)$", s)
        if m and speaker is not None and not speaker.main:
            things = [t.strip() for t in re.split(r",| and ", m.group(1)) if t.strip()]
            dup = [(t, claims[t]) for t in things if t in claims and claims[t] != speaker.id]
            for t in things:
                claims.setdefault(t, speaker.id)
            self.save({"claims": claims})
            if dup:
                return "movein_dup", {"item": dup[0][0], "name": self.name(dup[0][1])}
            return "movein_ok", {"items": join(things)}
        m = re.match(r"^who(?:'s| is) bringing (?:a |an |the )?(.+)$", s)
        if m:
            got = claims.get(m.group(1))
            return ("movein_who", {"item": m.group(1), "name": self.name(got)}) if got else ("movein_nobody", {"item": m.group(1)})
        if re.match(r"^what do we still need|^what(?:'s| is) still needed|^what haven't we got", s):
            left = [i for i in self.items() if i not in claims]
            return ("movein_left", {"items": join(left[:15])}) if left else ("movein_all", {})
        if re.match(r"^what(?:'s| is) everyone bringing", s):
            by: dict[str, list] = {}
            for t, p in claims.items():
                by.setdefault(self.name(p), []).append(t)
            return "movein_all_list", {"items": join(f"{n}: {join(ts)}" for n, ts in by.items()) or "nothing yet"}
        return None


# ================================================================ birthday stealth
class Secrets(Part):
    store = "secrets"

    def secrets(self, about: str | None = None) -> list[dict]:
        return [x for x in self.load({"secrets": []})["secrets"] if about is None or x["about"] == about]

    def visible_to(self, inst) -> list[dict]:
        return [x for x in self.secrets() if inst is None or x["about"] != inst.id]

    def birthdays(self) -> dict[str, str]:
        out = {}
        try:
            from .ai_plus import Facts

            raw = Facts(self.home).all().get("birthdays", {}).get("value") or {}
            for n, d in raw.items():
                inst = self.find(n)
                if inst:
                    out[inst.id] = str(d)
        except Exception:
            pass
        people = getattr(self.home, "people", None)
        for i in self.housemates():
            for f in (people.facts(i) if people else []):
                m = re.search(r"birthday(?: is)?(?: on)? (\w+ \d{1,2}|\d{1,2} \w+|\d{1,2}[-/]\d{1,2})", f.lower())
                if m:
                    out.setdefault(i.id, m.group(1))
        return out

    def tick(self, now):
        d = self.load({"secrets": []})
        for pid, bday in self.birthdays().items():
            day = _month_day(bday, now)
            if day is None:
                continue
            left = (day - now.date()).days
            key = f"{pid}:{day}"
            if left == 7 and key not in d.get("told", []) and now.hour >= 10:
                d.setdefault("told", []).append(key)
                self.save(d)
                plans = join(x["text"] for x in self.secrets(pid)) or "nothing yet"
                target = self.home.instances.all.get(pid)
                self.tell(self.others(target), "secret_week", title="Birthday (shh)", name=self.name(pid), plans=plans)

    def heard(self, said, room, speaker):
        s = clean(said)
        who = self.who_rx()
        m = re.match(rf"^(?:keep (?:this|it) )?secret from ({who})[:,]? (.+)$", s) or \
            re.match(rf"^(?:don't tell|do not tell) ({who})[:,]? (.+)$", s)
        if m:
            target = self.find(m.group(1))
            if speaker is not None and target.id == speaker.id:
                return None
            d = self.load({"secrets": []})
            d["secrets"].append({"about": target.id, "text": m.group(2), "by": getattr(speaker, "id", ""),
                                 "time": clock.now().strftime("%Y-%m-%d")})
            self.save(d)
            return "secret_saved", {"name": target.name}
        m = re.match(rf"^what are the (?:secret|surprise) plans(?: for ({who}))?$", s)
        if m:
            target = self.find(m.group(1)) if m.group(1) else None
            asker_room_owner = self.home.instances.for_room(room)
            rooms = getattr(self.home, "rooms", None)
            here = set(rooms.people_in(room)) if rooms is not None and room else set()
            items = [x for x in self.visible_to(speaker) if (target is None or x["about"] == target.id)
                     and x["about"] != asker_room_owner.id and x["about"] not in here]
            if not items:
                return "secret_none", {}
            return "secret_list", {"items": join(f"for {self.name(x['about'])}: {x['text']}" for x in items)}
        return None


def _month_day(text: str, now: datetime):
    from datetime import date

    t = text.strip().lower()
    for fmt in ("%m-%d", "%m/%d", "%B %d", "%b %d", "%d %B", "%d %b"):
        try:
            d = datetime.strptime(t, fmt)
            day = date(now.year, d.month, d.day)
            return day if day >= now.date() else date(now.year + 1, d.month, d.day)
        except ValueError:
            continue
    return None
