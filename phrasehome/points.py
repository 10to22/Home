"""House points, house jobs, and who owes whom.

Points    +1 for every good thing someone does willingly ("I did the dishes", "I took the bins out"
          when it wasn't my turn, "Parker cleaned the bathroom", running errands for others, letting a
          locked-out housemate in); -1 for every job someone was given and didn't do (their bin night,
          their party cleanup, any job given with "Finn, your job is to clean the bathroom by Sunday",
          leaving the front door unlocked on the way out). Your own jobs done on time are just done: 0.
          "that's a lie" / "Finn didn't do that" (from someone else, within 10 minutes) takes it back.
          At most `max_claims_per_day` self-claimed points a day. "how many points do I have",
          "house points", "who's winning". The leaderboard is on /house; the week's winner is announced
          on Sunday evening.
Jobs      "Finn, your job is to clean the bathroom by Sunday", "give Parker the dishes by tonight",
          "I've done my job" / "bathroom's clean". Overdue: a reminder, then -1.
Ledger    bets ("bet: Finn bets Parker a coffee it rains tomorrow", "Parker won the bet") and money
          (takeout splits): "who owes what", "Finn paid Parker back", "settle up".

points:
  max_claims_per_day: 3
  winner: { day: sun, time: "19:30" }
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta

from . import clock
from .life_base import Part, clean, join

GOOD_DEEDS = (r"(?:did|done|washed|cleaned|cleared|tidied|vacuumed|hoovered|mopped|swept|wiped|dusted|scrubbed|"
              r"emptied|took out|taken out|put out|brought in|unloaded|loaded|folded|bought|got|picked up|cooked|"
              r"made|fixed|replaced|restocked|organised|organized|watered|fed|walked|let .+ in)")
DEED = re.compile(rf"^(?:i|i've|i have|i just|i just did)\s+(?:just )?{GOOD_DEEDS}\b\s*(.*)$")


class Points(Part):
    section = "points"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.max_claims = int(self.cfg.get("max_claims_per_day", 3))
        w = self.cfg.get("winner") or {}
        self.win_day, self.win_at = str(w.get("day", "sun")), str(w.get("time", "19:30"))
        self._announced = ""

    # ---------- the points ----------
    def _data(self) -> dict:
        d = self.load({})
        d.setdefault("points", [])
        d.setdefault("jobs", [])
        d.setdefault("owes", [])
        return d

    def add(self, pid: str, delta: int, why: str, by: str = "", willing: bool = False) -> dict:
        with self._lock:
            d = self._data()
            row = {"id": uuid.uuid4().hex[:6], "person": pid, "delta": int(delta), "why": why, "by": by or "",
                   "willing": willing, "time": clock.now().strftime("%Y-%m-%d %H:%M:%S"), "t": time.time()}
            d["points"].append(row)
            d["points"] = d["points"][-3000:]
            self.save(d)
        self.home.events.log("points", person=pid, delta=int(delta), why=why)
        return row

    def good(self, inst, why: str, by=None) -> dict | None:
        """A good deed done willingly: +1 (unless it was their own job: then the job's just done)."""
        if inst is None or inst.main:
            return None
        if self.finish_job(inst, why):
            return {"job": True}
        by_id = getattr(by, "id", by) or inst.id
        if by_id == inst.id:
            today = clock.now().strftime("%Y-%m-%d")
            mine = [r for r in self._data()["points"] if r["person"] == inst.id and r["by"] == inst.id
                    and r["delta"] > 0 and r["time"].startswith(today)]
            if len(mine) >= self.max_claims:
                return {"capped": True}
        return self.add(inst.id, 1, why, by_id, willing=True)

    def bad(self, inst_or_id, why: str) -> dict:
        pid = getattr(inst_or_id, "id", inst_or_id)
        return self.add(str(pid), -1, why)

    def take_back(self, inst, by=None, minutes: float = 10) -> dict | None:
        with self._lock:
            d = self._data()
            cut = time.time() - minutes * 60
            for r in reversed(d["points"]):
                if r["person"] == inst.id and r["delta"] > 0 and r.get("t", 0) >= cut and r["by"] != getattr(by, "id", by):
                    d["points"].remove(r)
                    self.save(d)
                    self.home.events.log("points.revoked", person=inst.id, why=r["why"])
                    return r
        return None

    def totals(self, days: int | None = None) -> dict[str, int]:
        since = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d") if days else ""
        out = {i.id: 0 for i in self.housemates()}
        for r in self._data()["points"]:
            if r["time"] >= since:
                out[r["person"]] = out.get(r["person"], 0) + r["delta"]
        return out

    def board(self, days: int | None = None) -> list[tuple[str, int]]:
        return sorted(((self.name(p), n) for p, n in self.totals(days).items()), key=lambda x: -x[1])

    def recent(self, n: int = 20) -> list[dict]:
        return [{**r, "name": self.name(r["person"])} for r in self._data()["points"][-n:]][::-1]

    # ---------- jobs ----------
    def give_job(self, inst, task: str, due: datetime, by: str = "", kind: str = "job") -> dict:
        with self._lock:
            d = self._data()
            job = {"id": uuid.uuid4().hex[:6], "person": inst.id, "task": task.strip(), "due": due.isoformat(timespec="minutes"),
                   "by": by, "kind": kind, "done": False, "missed": False, "reminded": False}
            d["jobs"].append(job)
            d["jobs"] = [j for j in d["jobs"] if not (j["done"] or j["missed"]) or
                         datetime.fromisoformat(j["due"]) > clock.now() - timedelta(days=30)]
            self.save(d)
        return job

    def jobs(self, inst=None, open_only: bool = True) -> list[dict]:
        return [j for j in self._data()["jobs"] if (inst is None or j["person"] == inst.id)
                and (not open_only or not (j["done"] or j["missed"]))]

    def finish_job(self, inst, said: str) -> dict | None:
        """Their own open job that this sentence is about (or their only one, for "I've done my job")."""
        mine = self.jobs(inst)
        if not mine:
            return None
        words = set(re.findall(r"[a-z]+", said.lower())) - {"the", "a", "my", "i", "did", "done", "have", "i've", "out"}
        hit = next((j for j in mine if words & set(re.findall(r"[a-z]+", j["task"].lower()))), None)
        if hit is None and re.search(r"\bmy (?:job|jobs|chore|task|bit)\b", said.lower()):
            hit = mine[0]
        if hit is None:
            return None
        with self._lock:
            d = self._data()
            for j in d["jobs"]:
                if j["id"] == hit["id"]:
                    j["done"] = True
            self.save(d)
        self.home.events.log("job.done", person=inst.id, task=hit["task"])
        return hit

    def tick(self, now) -> None:
        late = []
        with self._lock:
            d = self._data()
            changed = False
            for j in d["jobs"]:
                if j["done"] or j["missed"]:
                    continue
                due = datetime.fromisoformat(j["due"])
                if due.tzinfo is None:
                    due = due.astimezone()
                if now >= due - timedelta(hours=1) and not j["reminded"]:
                    j["reminded"] = changed = True
                    late.append(("soon", j))
                if now >= due + timedelta(hours=float(self.cfg.get("grace_hours", 2))):
                    j["missed"] = changed = True
                    late.append(("missed", j))
            if changed:
                self.save(d)
        for what, j in late:
            inst = self.home.instances.all.get(j["person"])
            if inst is None:
                continue
            if what == "soon":
                self.say("job_reminder", room=self.room_of(inst), name=inst.name, task=j["task"])
                self.tell([inst], "job_reminder", title="Your job", name=inst.name, task=j["task"])
            else:
                self.bad(inst, f"didn't do: {j['task']}")
                self.tell([inst], "job_missed", title="House points", name=inst.name, task=j["task"])
        self._weekly(now)

    def _weekly(self, now) -> None:
        week = now.strftime("%Y-%W")
        if week == self._announced or not clock.day_matches(self.win_day, now.date()):
            return
        if now.strftime("%H:%M") < self.win_at:
            return
        self._announced = week
        board = [b for b in self.board(7) if b[1] != 0]
        if not board:
            return
        top, n = board[0]
        self.home.voice.speak("points_winner", rooms=self.shared_rooms(), name=top, points=n)
        self.tell(self.housemates(), "points_winner", title="House points", name=top, points=n)
        birds = self.early_birds(7)
        if birds:
            self.home.voice.speak("up_first_week", rooms=self.shared_rooms(), name=birds[0][0], n=birds[0][1])

    # ---------- up first ----------
    UP_EVENTS = ("wake.up", "bed.out", "person.room", "presence.arrived")

    def on_event(self, ev: dict) -> None:
        """The first person up each morning (4am-noon) gets a point: their alarm saw them get up, they left
        their bed, or they showed up in another room."""
        if ev["kind"] not in self.UP_EVENTS or self.cfg.get("up_first", True) is False:
            return
        now = clock.now()
        if not 4 <= now.hour < 12:
            return
        pid = str(ev.get("instance") or ev.get("person") or "")
        inst = self.home.instances.all.get(pid) or self.find(pid)
        if inst is None or inst.main:
            return
        if ev["kind"] == "person.room" and ev.get("room") in inst.rooms:
            return  # still in their own room: not up yet
        if ev["kind"] == "presence.arrived":
            return  # coming home isn't getting up
        day = now.strftime("%Y-%m-%d")
        with self._lock:
            d = self._data()
            if d.get("up_first", {}).get("day") == day:
                return
            d["up_first"] = {"day": day, "person": inst.id}
            d.setdefault("up_log", []).append({"day": day, "person": inst.id})
            d["up_log"] = d["up_log"][-200:]
            self.save(d)
        self.add(inst.id, 1, "first up this morning", "house", willing=True)
        self.home.voice.speak("up_first", room=self.room_of(inst), name=inst.name)

    def early_birds(self, days: int = 7) -> list[tuple[str, int]]:
        since = (clock.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        c: dict[str, int] = {}
        for r in self._data().get("up_log", []):
            if r["day"] >= since:
                c[r["person"]] = c.get(r["person"], 0) + 1
        return sorted(((self.name(p), n) for p, n in c.items()), key=lambda x: -x[1])

    # ---------- what people say ----------
    def heard(self, said: str, room, speaker):
        s = clean(said)
        who = self.who_rx()
        me = speaker if speaker is not None and not speaker.main else None
        m = re.match(rf"^(?:shout ?out|big ups|props|kudos|thanks|thank you|cheers)(?: to)? ({who})(?: (?:for|because) (.+))?$", s) \
            if re.match(r"^(?:shout ?out|big ups|props|kudos)", s) else None
        if m:
            inst = self.find(m.group(1))
            why = (m.group(2) or "being a legend").strip()
            if me is not None and inst.id == me.id:
                return "shout_self", {}
            giver = getattr(me, "id", "house")
            today = clock.now().strftime("%Y-%m-%d")
            given = [r for r in self._data()["points"] if r["by"] == giver and r["why"].startswith("shout-out")
                     and r["time"].startswith(today)]
            if me is not None and len(given) >= 3:
                return "shout_capped", {}
            self.add(inst.id, 1, f"shout-out: {why}", giver, willing=True)
            self.home.voice.speak("shout_out", rooms=self.shared_rooms(), name=inst.name, why=why)
            self.tell([inst], "shout_out", title="Shout-out", name=inst.name, why=why)
            return "done"
        if re.match(r"^who(?:'s| is| was) (?:up first|the early bird)|^early birds?$", s):
            b = self.early_birds(7)
            return ("up_first_week", {"name": b[0][0], "n": b[0][1]}) if b else ("points_board", {"board": "nobody yet"})
        if re.match(r"^(?:how many|what are my|my) (?:house )?points(?: do i have)?$|^how many points do i have$", s):
            if me is None:
                return self._board(room)
            n = self.totals().get(me.id, 0)
            return "points_mine", {"name": me.name, "points": n, "place": self._place(me.id)}
        if re.match(r"^(?:house points|points|the leaderboard|points leaderboard|who's winning|who is winning|"
                    r"who has the most points|scores?)$", s):
            return self._board(room)
        m = re.match(rf"^(?:that's a lie|no (?:they|he|she) didn't|liar|lies|({who}) didn't (?:do|actually do) (?:that|it|the \w+))$", s)
        if m:
            target = self.find(m.group(1)) if m.group(1) else self._last_claimer(exclude=me)
            if target is None:
                return None
            got = self.take_back(target, by=me)
            return ("points_revoked", {"name": target.name}) if got else ("points_nothing_to_revoke", {"name": target.name})
        m = re.match(rf"^({who}),? (?:your job is to|you(?:'re| are) on|you need to|can you) (.+?)(?: by (.+))?$", s) or \
            re.match(rf"^(?:give|assign) ({who}) (?:the job of |the )?(.+?)(?: by (.+))?$", s)
        if m:
            inst = self.find(m.group(1))
            from .study import parse_when

            due = parse_when(" " + (m.group(3) or "tonight"))
            if due is None or due < clock.now():
                due = clock.now().replace(hour=21, minute=0, second=0, microsecond=0)
                if due < clock.now():
                    due += timedelta(days=1)
            job = self.give_job(inst, m.group(2), due, getattr(speaker, "id", ""))
            self.tell([inst], "job_given", title="New job", name=inst.name, task=job["task"],
                      when=_when(due))
            return "job_given", {"name": inst.name, "task": job["task"], "when": _when(due)}
        if me is not None and re.match(r"^i(?:'ve| have)? (?:done|finished) (?:my|the) (?:job|jobs|chore|task|bit)$", s):
            got = self.finish_job(me, s)
            return ("job_done", {"name": me.name, "task": got["task"]}) if got else ("job_none", {"name": me.name})
        m = DEED.match(s)
        if m and me is not None:
            got = self.good(me, s, by=me)
            return self._said(me, got, s)
        m = re.match(rf"^({who}) (?:just )?{GOOD_DEEDS}\b\s*(.*)$", s)
        if m and not re.search(r"\b(?:didn't|never|not)\b", s):
            inst = self.find(m.group(1))
            if inst is None or (me is not None and inst.id == me.id):
                return None
            got = self.good(inst, s, by=me or "house")
            return self._said(inst, got, s)
        return None

    def _said(self, inst, got, s):
        if not got:
            return None
        if got.get("job"):
            return "job_done", {"name": inst.name, "task": s}
        if got.get("capped"):
            return "points_capped", {"name": inst.name}
        return "points_good", {"name": inst.name, "points": self.totals().get(inst.id, 0)}

    def _last_claimer(self, exclude=None):
        for r in reversed(self._data()["points"]):
            if r["delta"] > 0 and (exclude is None or r["person"] != exclude.id):
                return self.home.instances.all.get(r["person"])
        return None

    def _place(self, pid: str) -> str:
        order = [p for p, _ in sorted(self.totals().items(), key=lambda x: -x[1])]
        n = order.index(pid) + 1 if pid in order else len(order)
        return {1: "first", 2: "second", 3: "third"}.get(n, f"number {n}")

    def _board(self, room):
        b = self.board()
        return "points_board", {"board": join(f"{n} {p}" for n, p in b) or "nobody has any yet"}

    # ---------- who owes whom ----------
    def owe(self, debtor, creditor, what: str, amount: float | None = None, why: str = "") -> dict:
        with self._lock:
            d = self._data()
            row = {"id": uuid.uuid4().hex[:6], "from": debtor.id, "to": creditor.id, "what": what, "amount": amount,
                   "why": why, "time": clock.now().strftime("%Y-%m-%d"), "paid": False}
            d["owes"].append(row)
            self.save(d)
        return row

    def paid(self, debtor, creditor=None) -> int:
        n = 0
        with self._lock:
            d = self._data()
            for r in d["owes"]:
                if not r["paid"] and r["from"] == debtor.id and (creditor is None or r["to"] == creditor.id):
                    r["paid"] = True
                    n += 1
            self.save(d)
        return n

    def debts(self) -> list[dict]:
        return [r for r in self._data()["owes"] if not r["paid"]]

    def owes_text(self) -> str:
        money: dict[tuple[str, str], float] = {}
        things = []
        for r in self.debts():
            if r["amount"] is not None:
                k = (r["from"], r["to"])
                money[k] = money.get(k, 0.0) + float(r["amount"])
            else:
                things.append(f"{self.name(r['from'])} owes {self.name(r['to'])} {r['what']}")
        for (a, b), amt in list(money.items()):  # net it out between two people
            back = money.get((b, a), 0.0)
            if back and amt >= back:
                money[(a, b)], money[(b, a)] = amt - back, 0.0
        lines = [f"{self.name(a)} owes {self.name(b)} ${amt:.2f}".replace(".00", "") for (a, b), amt in money.items() if amt > 0.004]
        return join(lines + things)


def _when(due: datetime) -> str:
    today = clock.now().date()
    day = "today" if due.date() == today else "tomorrow" if due.date() == today + timedelta(days=1) else due.strftime("%A")
    return f"{day} at {clock.spoken_time(due)}"
