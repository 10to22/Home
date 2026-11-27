"""Looking out for each other: nights out, the safe word, finding your phone, new Wi-Fi devices,
and the party head count.

Night out      "we're going out, back by 2" (or "night out mode"): the people home right now are the group.
               At that time, anyone not back is checked on: their phone ("reply ok, or walking home 20 to the
               house topic") and the others get a calm, plain heads-up.
Safe word      safe_word: {phrase: "pineapple pizza"}: said anywhere, the house says nothing and quietly
               texts the others (or `tell:`): "Finn used the safe word in Finn's room. Check on them now."
Ring my phone  "where's my phone?", "ring my phone": an urgent ntfy notification makes it ring.
New devices    every 10 minutes the Wi-Fi's device list is checked; something new (that isn't a party guest)
               gets reported. Learns what's normal for the first day. Approve devices on /house.
Head count     party: {counter: {node: door, in: in_beam, out: out_beam}}, or "five more people just arrived" /
               "three people left": "how many people are here?". When the last guest leaves after quiet
               time, the doors lock.

safe_word: { phrase: "pineapple pizza", tell: [parker, jo] }
night_out: { back_by: "03:00" }
network: { every_minutes: 10, learn_hours: 24 }
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta

from . import clock
from .life_base import Part, clean, join

NUMS = {"one": 1, "a": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "a couple": 2, "a few": 3, "couple": 2, "few": 3}


class NightOut(Part):
    section = "night_out"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.out: dict | None = None

    def start(self, people: list, back_by: datetime) -> str:
        self.out = {"people": [i.id for i in people], "back_by": back_by.timestamp(), "checked": 0, "ok": []}
        self.home.events.log("night_out", people=",".join(self.out["people"]), back_by=back_by.strftime("%H:%M"))
        return f"night out: {join(i.name for i in people)}, back by {clock.spoken_time(back_by)}"

    def tick(self, now):
        o = self.out
        if not o:
            return
        late = (time.time() - o["back_by"]) / 60
        here = set(self.home.state.home_people())
        missing = [self.home.instances.all[p] for p in o["people"] if p not in here and p not in o["ok"]
                   and p in self.home.instances.all]
        if late >= 0 and not missing:
            self.out = None
            return
        if (late >= 15 and o["checked"] == 0) or (late >= 45 and o["checked"] == 1):
            o["checked"] += 1
            names = join(i.name for i in missing)
            vals = dict(names=names, time=clock.spoken_time(now), are="is" if len(missing) == 1 else "are")
            self.tell(missing, "night_out_check", title="Are you OK?", **vals)
            others = [self.home.instances.all[p] for p in here if p in self.home.instances.all and not
                      self.home.instances.all[p].main] + [i for i in self.housemates() if i.id in o["people"] and i not in missing]
            self.tell(list({i.id: i for i in others}.values()), "night_out_late", title="Not home yet", **vals)
            self.home.events.log("night_out.late", people=",".join(i.id for i in missing))
        if late > 6 * 60:
            self.out = None

    def ok(self, inst) -> bool:
        if self.out and inst is not None and inst.id in self.out["people"]:
            self.out["ok"].append(inst.id)
            return True
        return False

    def heard(self, said, room, speaker):
        s = clean(said)
        m = re.match(r"^(?:night out mode|we're (?:going|heading) out(?: tonight)?|we're off out)(?:,? (?:back|home) (?:by|around|at) (.+))?$", s)
        if m:
            from .study import parse_when

            if m.group(1):
                back = parse_when(" today at " + m.group(1).replace("am", " am").replace("pm", " pm"))
            else:
                t = clock.parse_hhmm(str(self.cfg.get("back_by", "03:00")))
                back = clock.now().replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if back is None:
                return None
            if back <= clock.now():
                back += timedelta(days=1)
            if back.hour < 12 and clock.now().hour >= 12 and back.date() == clock.now().date():
                back += timedelta(days=1)
            people = [self.find(p) for p in self.home.state.home_people()]
            people = [i for i in people if i and not i.main] or self.housemates()
            self.start(people, back)
            return "night_out_on", {"names": join(i.name for i in people), "time": clock.spoken_time(back)}
        if re.match(r"^(?:i'm |i am )?(?:ok|okay|fine|all good|safe)$", s) and self.ok(speaker):
            return "night_out_ok", {"name": speaker.name}
        return None


class SafeWord(Part):
    section = "safe_word"

    def phrase(self) -> str:
        return str(self.cfg.get("phrase") or "").strip().lower()

    def trigger(self, room: str | None, person: str | None = None) -> str:
        h = self.home
        inst = h.instances.all.get(person) if person in h.instances.all else h.instances.for_room(room)
        name = inst.name if inst and not inst.main else "Someone"
        tell = [self.find(p) for p in self.cfg.get("tell") or []]
        tell = [i for i in tell if i] or self.others(inst if inst and not inst.main else None)
        text = self.text("safe_word_alert", name=name, room=room or "the house")
        for i in tell:
            if i.id != getattr(inst, "id", None):
                h.notify_person(i, text, title="Safe word", priority=5)
        h.events.log("safe_word", room=room or "", person=getattr(inst, "id", ""))
        return "sent"  # and nothing is said out loud


class RingPhone(Part):
    def heard(self, said, room, speaker):
        if not re.match(r"^(?:where's|where is|find|ring|call) my phone|^i can't find my phone|^i lost my phone", clean(said)):
            return None
        inst = speaker if speaker is not None and not speaker.main else None
        if inst is None or not inst.phone:
            return "phone_unknown", {}
        self.home.notify_person(inst, "Here I am! The house is ringing your phone.", title="Found me", priority=5)
        return "phone_ringing", {"name": inst.name}


class Network(Part):
    section = "network"
    store = "network"

    def __init__(self, home) -> None:
        super().__init__(home)
        self._next = 0.0

    def neighbours(self) -> dict[str, str]:
        """{mac: ip} from the computer's neighbour table (what's been seen on the network lately)."""
        out: dict[str, str] = {}
        try:
            if platform.system() == "Linux" and shutil.which("ip"):
                text = subprocess.run(["ip", "neigh", "show"], capture_output=True, text=True, timeout=5).stdout
                for line in text.splitlines():
                    m = re.match(r"^(\S+) .*lladdr ([0-9a-f:]{17})", line)
                    if m and "FAILED" not in line:
                        out[m.group(2)] = m.group(1)
            elif shutil.which("arp"):
                text = subprocess.run(["arp", "-an"], capture_output=True, text=True, timeout=5).stdout
                for line in text.splitlines():
                    m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-f:]{11,17})", line)
                    if m:
                        out[":".join(x.zfill(2) for x in m.group(2).split(":"))] = m.group(1)
        except Exception:
            pass
        return {k: v for k, v in out.items() if not k.startswith(("ff:", "01:00:5e", "33:33"))}

    def check(self, seen: dict[str, str] | None = None) -> list[str]:
        seen = self.neighbours() if seen is None else seen
        d = self.load({})
        known = d.setdefault("known", {})
        first = d.setdefault("first", time.time())
        learning = time.time() - first < float(self.cfg.get("learn_hours", 24)) * 3600
        party = getattr(self.home.life.party, "active", None)
        new = [m for m in seen if m not in known]
        for mac in new:
            known[mac] = {"ip": seen[mac], "first": clock.now().strftime("%Y-%m-%d %H:%M"),
                          "label": "party guest" if party else ("" if learning else "new")}
        self.save(d)
        if learning or party or not new:
            return []
        for mac in new:
            self.home.events.log("network.new_device", mac=mac, ip=seen[mac])
        self.tell(self.housemates(), "network_new", title="New device on the Wi-Fi",
                  devices=join(f"{seen[m]} ({m})" for m in new))
        return new

    def approve(self, mac: str, label: str = "ok") -> None:
        d = self.load({})
        d.setdefault("known", {}).setdefault(mac, {})["label"] = label
        self.save(d)

    def tick(self, now):
        if self.cfg.get("enabled", True) is False or self.home.dry_run:
            return
        if time.time() >= self._next:
            self._next = time.time() + float(self.cfg.get("every_minutes", 10)) * 60
            self.check()


class HeadCount(Part):
    def __init__(self, home) -> None:
        super().__init__(home)
        self.count = 0

    def _cfg(self) -> dict:
        return (self.home.config.extra.get("party") or {}).get("counter") or {}

    def change(self, n: int) -> None:
        party = self.home.life.party
        self.count = max(0, self.count + n)
        if party.active and party.active.get("quieted") and self.count == 0 and n < 0:
            self.home.doors.set("all", "locked", reason="last guest left")
            self.home.voice.speak("headcount_last_out", room=party.active.get("room"))

    def on_event(self, ev):
        c = self._cfg()
        if ev["kind"] == "party.on":
            self.count = 0
        if ev["kind"] != "sensor" or not c or ev.get("node") != str(c.get("node", "")).lower():
            return
        if ev.get("state") == "pressed":
            n = int(ev.get("presses") or 1)
            if ev.get("input") == str(c.get("in", "")).lower():
                self.change(n)
            elif ev.get("input") == str(c.get("out", "")).lower():
                self.change(-n)

    def heard(self, said, room, speaker):
        s = clean(said)
        nums = "|".join(sorted((re.escape(k) for k in NUMS), key=len, reverse=True))
        m = re.match(rf"^(\d+|{nums}) (?:more )?(?:people|guests|of them|friends)? ?(?:just )?(arrived|came in|turned up|are here|left|went home|have left)$", s)
        if m and self.home.life.party.active:
            n = int(m.group(1)) if m.group(1).isdigit() else NUMS[m.group(1)]
            self.change(n if m.group(2) in ("arrived", "came in", "turned up", "are here") else -n)
            return "headcount", {"count": self.count}
        if re.match(r"^how many (?:people|guests) are (?:here|in the house|at the party)", s):
            home_n = len([p for p in self.home.state.home_people()])
            return "headcount_total", {"count": self.count, "housemates": home_n}
        return None
