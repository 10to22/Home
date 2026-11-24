"""The AI keeps the house running smoothly in the background:

- Daily diary: each evening a short, friendly write-up of the day (who came and went, deliveries,
  doorbell, chores, energy, weather), saved to data/diary/ and shown on the dashboard.
- Proactive watcher: every so often it reviews what's been happening and speaks up only when
  something is genuinely worth mentioning (a door unlocked late at night, the washer done hours
  ago, a package still outside at dark, a module offline).
- Welcome-back summary: when the first person gets home after the house was empty, it tells them
  what happened while they were out.

diary: { time: "21:30" }
watcher: { every_minutes: 60, quiet_hours: "22:30-07:00" }
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from . import ai, clock

log = logging.getLogger("phrasehome.diary")

NOISE = {"agenda.added", "assistant", "routine", "schedule", "automation", "sensor", "person.room",
         "room.occupied", "room.empty", "tag.moved", "weather.advisory"}


def notable(events: list[dict]) -> list[dict]:
    keep = []
    for e in events:
        if e["kind"] in NOISE:
            continue
        keep.append({k: v for k, v in e.items() if k not in ("photo",) and v not in (None, "")})
    return keep


class Diary:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("diary") or {})
        self.wcfg = dict(home.config.extra.get("watcher") or {})
        self.folder = Path(home.config.resolve_path(home.config.data_dir)) / "diary"
        self._last_watch = time.monotonic()
        self._said: list[str] = []
        self._away_since: str | None = None
        self._written = ""
        self._running = False
        home.diary = self
        home.events.subscribe(self._on_event)

    # ---------- diary ----------
    def write(self, day: str | None = None) -> str:
        day = day or clock.now().strftime("%Y-%m-%d")
        events = [e for e in self.home.events.query(limit=500) if e["time"].startswith(day)]
        extra = []
        if getattr(self.home, "energy", None):
            extra.append(f"Energy today: {self.home.energy.report(1)}")
        if getattr(self.home, "weather", None):
            extra.append(f"Weather: {self.home.weather.summary(0)}")
        names = {i.id: i.name for i in self.home.instances.all.values()}
        text = ai.ask(self.home, (
            f"Write the house's diary entry for {day}: 4 to 8 short, warm sentences in plain prose, "
            "past tense, like a friendly butler's log. Mention who came and went, visitors and "
            "deliveries, notable things that happened, and anything that needs attention tomorrow. "
            "Skip routine noise. Don't invent anything.\n"
            f"People (ids to names): {json.dumps(names)}\n{chr(10).join(extra)}\n"
            f"Events: {json.dumps(notable(events))[:30000]}"), effort="low")
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{day}.md").write_text(f"# {day}\n\n{text}\n")
        self.home.events.log("diary.written", date=day)
        return text

    def read(self, day: str | None = None) -> str:
        day = day or clock.now().strftime("%Y-%m-%d")
        f = self.folder / f"{day}.md"
        return f.read_text().split("\n", 2)[-1].strip() if f.exists() else ""

    def days(self) -> list[str]:
        return sorted((p.stem for p in self.folder.glob("*.md")), reverse=True) if self.folder.exists() else []

    # ---------- proactive watcher ----------
    WATCH_SCHEMA = ai.obj({"say_something": {"type": "boolean"},
                           "message": {"type": "string"},
                           "urgent": {"type": "boolean"},
                           "everyone": {"type": "boolean"}})

    def watch(self) -> dict[str, Any] | None:
        h = self.home
        from .reports import open_things

        opened, unlocked, _ = open_things(h)
        state = {"time": clock.now().strftime("%A %H:%M"), "mode": h.state.mode,
                 "people_home": h.state.home_people(), "open": opened, "unlocked": unlocked,
                 "offline": sorted(h.sensors.offline) if h.sensors else [],
                 "agenda_today": {i.name: [x["text"] or x["label"] for x in h.instances.items(i)[:5]]
                                  for i in h.instances.all.values()}}
        res = ai.ask(h, (
            "You quietly watch over a family home. Look at the last two hours and the current state. "
            "Only say something if it's genuinely useful and not already handled: e.g. a door unlocked "
            "or open late at night, a finished washer nobody emptied for hours, a package left out "
            "after dark, a module offline, motion while everyone's away. Most of the time, say nothing. "
            f"Things you already said today (don't repeat): {json.dumps(self._said[-10:])}.\n"
            f"State: {json.dumps(state)}\n"
            f"Recent events: {json.dumps(notable(h.events.query(limit=150)))[:20000]}\n"
            "If you speak, the message is read aloud: one or two short sentences."),
            schema=self.WATCH_SCHEMA)
        if not res.get("say_something") or not res.get("message"):
            return None
        self._said.append(res["message"])
        quiet = clock.in_window(self.wcfg.get("quiet_hours", "22:30-07:00"), clock.now())
        h.events.log("watcher", message=res["message"], urgent=res.get("urgent", False))
        if not quiet or res.get("urgent"):
            h.voice.speak(res["message"], everywhere=bool(res.get("everyone", True)), urgent=bool(res.get("urgent")))
        if res.get("urgent") or (quiet and not h.state.home_people()):
            h.notify_person(h.instances.main, res["message"], "House")
            for inst in h.instances.all.values():
                if inst.phone and not inst.main:
                    h.notify_person(inst, res["message"], "House")
        return res

    # ---------- welcome back ----------
    def _on_event(self, ev: dict) -> None:
        k = ev["kind"]
        if k == "presence.everyone_left" or (k == "mode" and ev.get("mode") in ("away", "vacation")):
            self._away_since = self._away_since or ev["time"]
        elif k == "presence.first_home" and self._away_since:
            since, self._away_since = self._away_since, None
            threading.Thread(target=self.welcome_back, args=(since, ev.get("person")), daemon=True).start()

    def welcome_back(self, since: str, person: str | None = None) -> str:
        events = [e for e in self.home.events.query(limit=500) if e["time"] >= since]
        interesting = [e for e in notable(events) if not e["kind"].startswith(("presence", "mode", "alarm.arm"))]
        if not interesting:
            return ""
        text = ai.ask(self.home, (
            "Someone just got home. In two or three short spoken sentences, tell them what happened "
            "while the house was empty (deliveries, visitors, the doorbell, anything unusual). If "
            f"nothing much happened, say so in one sentence. Events: {json.dumps(interesting)[:15000]}"))
        room = next(iter(self.home.config.doors.values()), {}).get("room")
        self.home.voice.speak(text, room=room)
        self.home.events.log("welcome_back.summary", person=person or "")
        return text

    # ---------- loop ----------
    def tick(self) -> None:
        now = clock.now()
        if self.cfg.get("enabled", True) and now.strftime("%H:%M") == self.cfg.get("time", "21:30") \
                and self._written != now.strftime("%Y-%m-%d"):
            self._written = now.strftime("%Y-%m-%d")
            self.write()
        every = float(self.wcfg.get("every_minutes", 60)) * 60
        if self.wcfg.get("enabled", True) and time.monotonic() - self._last_watch > every:
            self._last_watch = time.monotonic()
            self.watch()

    def start(self) -> None:
        if self.home.assistant is None and not getattr(self.home, "_ai_client", None):
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.tick()
            except Exception as e:
                log.warning("diary/watcher: %s", e)
            time.sleep(30)
