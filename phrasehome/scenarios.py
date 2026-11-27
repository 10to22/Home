"""Simulator scenarios: play a whole evening (or week) against your config in seconds, and see everything
the house said, sent and did. Nothing real is touched: devices are dry-run, data goes to a temp folder.

    phrasehome scenario list
    phrasehome scenario run friday_night
    phrasehome scenario run all

Your own, in config.yaml (p1, p2, p3 are your first three housemates):

scenarios:
  bin_night_test:
    - { time: "thu 18:55" }
    - { wait: 10m }
    - { say: "the bins are out", person: p2 }
    - { expect: "bins" }

Steps: time ("fri 21:00" / "21:00"), wait ("10m", "2h"), say (with person / room), arrive / leave (a person),
sensor ({node, input, value | state}), event ({kind: ..., ...}), expect (words that must have been said or sent
since the last expect).
"""

from __future__ import annotations

import re
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import clock

BUILTIN: dict[str, list[dict[str, Any]]] = {
    "friday_night": [
        {"time": "fri 20:00"}, {"arrive": "p1"}, {"arrive": "p2"},
        {"say": "party mode", "room": "shared"}, {"expect": "party"},
        {"say": "what's the wifi password", "person": "p2"},
        {"wait": "2h30m"}, {"expect": "quiet time"},
        {"wait": "40m"}, {"say": "party's over", "room": "shared"}, {"expect": "cleanup"},
        {"leave": "p2"}, {"leave": "p1"},
    ],
    "exam_week": [
        {"time": "18:00"}, {"arrive": "p1"}, {"arrive": "p2"},
        {"say": "i've got a calc exam tomorrow at 9", "person": "p1"}, {"expect": "exam"},
        {"wait": "1h10m"}, {"expect": "heads up"},
        {"wait": "2h30m"}, {"expect": "quiet"},
        {"time": "+1d 07:45"}, {"expect": "good luck"},
    ],
    "walk_home_late": [
        {"time": "22:00"}, {"arrive": "p1"},
        {"say": "p2's walking home from the library", "person": "p1"}, {"expect": "expect"},
        {"wait": "40m"}, {"expect": "isn't"},
        {"arrive": "p2"}, {"expect": "home safe"},
    ],
    "points_week": [
        {"time": "mon 17:00"}, {"arrive": "p1"}, {"arrive": "p2"},
        {"say": "i did the dishes", "person": "p1"}, {"expect": "plus one"},
        {"say": "p2 your job is to vacuum the lounge by tonight", "person": "p1"}, {"expect": "vacuum"},
        {"wait": "8h"}, {"expect": "minus one"},
        {"say": "house points", "person": "p2"}, {"expect": "points"},
    ],
    "locked_out": [
        {"time": "16:00"}, {"arrive": "p2"},
        {"phone": "locked out", "person": "p1"}, {"expect": "locked out"},
        {"say": "i'm coming", "person": "p2"}, {"event": {"kind": "door.unlocked", "door": "front"}},
    ],
}


class _Clock:
    def __init__(self, monkey) -> None:
        self.offset = 0.0
        self._patch = monkey
        real_mono, real_time, real_now = time.monotonic, time.time, clock.now
        self._real = (real_mono, real_time, real_now)
        monkey(time, "monotonic", lambda: real_mono() + self.offset)
        monkey(time, "time", lambda: real_time() + self.offset)
        monkey(clock, "now", lambda: real_now() + timedelta(seconds=self.offset))

    def now(self) -> datetime:
        return clock.now()

    def to(self, target: datetime) -> float:
        delta = (target - clock.now()).total_seconds()
        self.offset += max(0.0, delta)
        return delta


def _parse_time(spec: str, now: datetime) -> datetime:
    s = spec.strip().lower()
    days = 0
    m = re.match(r"^\+(\d+)d\s*(.*)$", s)
    if m:
        days, s = int(m.group(1)), m.group(2) or now.strftime("%H:%M")
    wd = None
    m = re.match(r"^(mon|tue|wed|thu|fri|sat|sun)\w*\s+(.+)$", s)
    if m:
        wd, s = clock.DAY_NAMES.index(m.group(1)), m.group(2)
    t = clock.parse_hhmm(s)
    target = (now + timedelta(days=days)).replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
    if wd is not None:
        target += timedelta(days=(wd - target.weekday()) % 7)
    if target < now:
        target += timedelta(days=7 if wd is not None else 1)
    return target


def _duration(spec: str) -> float:
    total = 0.0
    for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", spec.lower()):
        total += float(n) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def run(config_path: str | Path, name: str, show=print) -> dict:
    from .config import load_config
    from .dispatcher import Dispatcher

    cfg = load_config(config_path)
    steps = (cfg.extra.get("scenarios") or {}).get(name) or BUILTIN.get(name)
    if steps is None:
        raise KeyError(f"no scenario called {name} (have: {', '.join(names(cfg))})")
    tmp = Path(tempfile.mkdtemp(prefix="phrasehome-scenario-"))
    cfg.data_dir = str(tmp)
    cfg.web = {"enabled": False}
    patches: list[tuple] = []

    def monkey(obj, attr, val):
        patches.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, val)

    log: list[str] = []
    results = {"name": name, "passed": 0, "failed": 0, "log": log}
    home = None
    try:
        ck = _Clock(monkey)
        home = Dispatcher(cfg, dry_run=True)
        home.voice.dry_run = True
        home.life.sync = True
        people = [i for i in home.instances.all.values() if not i.main]
        alias = {f"p{n + 1}": i for n, i in enumerate(people)}
        shared = (home.instances.main.rooms or [home.default_room() or "living room"])[0]
        heard: list[str] = []
        real_speak = home.voice.speak

        def stamp() -> str:
            return clock.now().strftime("%a %H:%M")

        def speak(key, **v):
            text = real_speak(key, **v)
            where = v.get("room") or ", ".join(v.get("rooms") or []) or ("everywhere" if v.get("everywhere") else "the hub")
            if text:
                log.append(f"[{stamp()}] says in {where}: {text}")
                heard.append(text.lower())
            return text

        home.voice.speak = speak
        monkey(home, "notify_person", lambda inst, msg, title="", **kw: (log.append(f"[{stamp()}] {inst.name}'s phone: "
                                                                                    f"{title + ': ' if title else ''}{msg}"),
                                                                         heard.append(msg.lower())))

        def sub(text: str) -> str:
            for k, i in alias.items():
                text = re.sub(rf"\b{k}\b", i.name.lower(), text)
            return text

        def tick_to(target: datetime) -> None:
            while clock.now() < target:
                step = min(60.0, (target - clock.now()).total_seconds())
                ck.offset += max(step, 1.0)
                home.timeline.run_due()
                home.scheduler.tick()
                home.life.tick()

        from .matcher import Matcher
        from .understanding import candidates_with_aliases

        s = cfg.settings
        matcher = Matcher(candidates_with_aliases(cfg), s.match_mode, s.match_threshold, s.block_words)
        for st in steps:
            if "time" in st:
                target = _parse_time(str(st["time"]), clock.now())
                ck.to(target)
                home.life.tick()
                log.append(f"[{stamp()}] --- time is now {stamp()}")
            elif "wait" in st:
                tick_to(clock.now() + timedelta(seconds=_duration(str(st["wait"]))))
            elif "arrive" in st or "leave" in st:
                who = alias.get(str(st.get("arrive") or st.get("leave"))) or home.life.people.find(st.get("arrive") or st.get("leave"))
                home.state.set_presence(who.id, "home" if "arrive" in st else "away", "scenario")
                log.append(f"[{stamp()}] {who.name} {'gets home' if 'arrive' in st else 'leaves'}")
            elif "sensor" in st:
                sv = dict(st["sensor"])
                home.events.log("sensor", **sv)
            elif "event" in st:
                ev = dict(st["event"])
                home.events.log(ev.pop("kind"), **{k: sub(str(v)) for k, v in ev.items()})
            elif "phone" in st:
                who = alias.get(str(st.get("person"))) or home.life.people.find(st.get("person"))
                log.append(f"[{stamp()}] {who.name} texts the house: {st['phone']}")
                home.life.from_phone(sub(str(st["phone"])), title=who.name)
            elif "say" in st:
                who = alias.get(str(st.get("person"))) if st.get("person") else None
                room = str(st.get("room") or (who.rooms[0] if who and who.rooms else shared))
                room = shared if room == "shared" else room
                text = sub(str(st["say"]))
                log.append(f"[{stamp()}] {who.name if who else 'someone'} in {room}: \"{text}\"")
                m = matcher.match(text)
                if m:
                    home.run_action(m.command.action, {**m.slots, "here": room, **({"person": who.id} if who else {})}, m.phrase)
                elif not home.life.quick(text, room, who.id if who else None):
                    log.append(f"[{stamp()}]   (not a command; the AI would get this)")
            elif "expect" in st:
                want = sub(str(st["expect"])).lower()
                ok = any(want in h for h in heard)
                results["passed" if ok else "failed"] += 1
                log.append(f"[{stamp()}] {'PASS' if ok else 'FAIL'}: expected \"{want}\"")
                heard.clear()
        return results
    finally:
        if home is not None:
            try:
                home.close()
            except Exception:
                pass
        for obj, attr, val in reversed(patches):
            setattr(obj, attr, val)
        shutil.rmtree(tmp, ignore_errors=True)
        for line in log:
            show(line)


def names(cfg) -> list[str]:
    return sorted(set(BUILTIN) | set((cfg.extra.get("scenarios") or {})))


def main(args) -> int:
    from .config import load_config

    cfg = load_config(args.config)
    if args.action == "list" or not args.name:
        for n in names(cfg):
            print(n)
        return 0
    failed = 0
    for n in (names(cfg) if args.name == "all" else [args.name]):
        print(f"\n===== {n} =====")
        r = run(args.config, n)
        print(f"----- {n}: {r['passed']} passed, {r['failed']} failed")
        failed += r["failed"]
    return 1 if failed else 0
