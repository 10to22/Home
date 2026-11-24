"""Backends that are always available, picked from the action's keys (see config.BUILTIN_BACKENDS).

Every built-in gets the context of what triggered it in action["_context"], e.g. {"here": room}
for a voice command, {"name": "Sam", "door": "front"} for a door scan.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .. import clock
from .base import Backend

log = logging.getLogger("phrasehome")


def _ctx(action: dict[str, Any], *skip: str) -> dict[str, Any]:
    out = dict(action.get("_context", {}))
    out.update({k: v for k, v in action.items() if k not in ("backend", "_context", *skip)})
    return out


def _on(v: Any) -> bool:
    return v is True or str(v).lower() in ("on", "true", "yes", "1", "start")


class _Builtin(Backend):
    @property
    def home(self):
        return self.config.home

    def room(self, action) -> str | None:
        r = action.get("room") or action.get("_context", {}).get("here")
        return None if r in (None, "", "all", "everywhere") else str(r).lower()


class DoorsBackend(_Builtin):
    """{door: front, state: locked}   door can be a name or "all"."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        return self.home.doors.set(action["door"], action.get("state", "locked"),
                                   reason=action.get("reason", phrase or "command"))


class RoutineBackend(_Builtin):
    """{routine: lockdown}: run each step in order. Steps can be any action, {wait: seconds},
    another {routine: ...} or {if: ..., then: [...], else: [...]}."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        name = action["routine"]
        steps = self.config.routines.get(name)
        if steps is None:
            raise ValueError(f"unknown routine '{name}'")
        self.home.events.log("routine", name=name)
        failed = self.home.run_steps(steps, _ctx(action, "routine"), phrase)
        return f"routine {name}" + (f" ({failed} steps failed)" if failed else "")


class LogicBackend(_Builtin):
    """{if: {mode: away}, then: [...], else: [...]}"""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        failed = self.home.run_steps([action], _ctx(action, "if", "then", "else"), phrase)
        return "done" if not failed else f"{failed} steps failed"


class VoiceBackend(_Builtin):
    """{speak: phrase_key_or_text}  + optional rooms: [kitchen], everywhere: true, urgent: true.
    Without rooms it speaks in the room that triggered it (or the hub's own speaker)."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        values = _ctx(action, "speak", "rooms", "everywhere", "urgent")
        rooms = action.get("rooms")
        if isinstance(rooms, str):
            rooms = [rooms]
        return self.home.voice.speak(
            str(action["speak"]), room=self.room(action), rooms=rooms,
            everywhere=_on(action.get("everywhere", False)), urgent=_on(action.get("urgent", False)),
            **{k: v for k, v in values.items() if k not in ("room",)})


class TimerBackend(_Builtin):
    """{timer: set, minutes: 10, label: pasta} | {timer: cancel} | {timer: status}"""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        room = self.room(action)
        inst = self.home.instances.for_room(room)
        what = str(action.get("timer", "set")).lower()
        voice = self.home.voice
        if what == "set":
            secs = float(action.get("minutes", 0) or 0) * 60 + float(action.get("seconds", 0) or 0) \
                + float(action.get("hours", 0) or 0) * 3600
            if secs <= 0:
                raise ValueError("timer needs minutes, seconds or hours")
            label = str(action.get("label", "") or "")
            self.home.instances.add(inst, "timer", clock.now() + timedelta(seconds=secs), label=label,
                                    room=room)
            return voice.speak("timer_set", room=room, duration=clock.spoken_duration(secs), label=label)
        timers = self.home.instances.items(inst, ("timer",))
        if room:
            timers = [t for t in timers if t.get("room") in (room, None)] or timers
        if what == "cancel":
            hit = self.home.instances.cancel(inst, str(action.get("label", "")), ("timer",))
            return voice.speak("timer_cancelled" if hit else "no_timers", room=room)
        if what in ("status", "list"):
            if not timers:
                return voice.speak("no_timers", room=room)
            now = clock.now()
            parts = []
            for t in timers[:3]:
                left = (clock.datetime.fromisoformat(t["due"]) - now).total_seconds()
                parts.append(f"{t['label'] + ' ' if t['label'] else ''}{clock.spoken_duration(max(left, 0))}")
            return voice.speak("timer_status", room=room, left=", and ".join(parts))
        raise ValueError("timer must be set, cancel or status")


class AlarmBackend(_Builtin):
    """{alarm: stop} | {alarm: snooze, minutes: 9} | {alarm: set, at: "7:00", repeat: weekdays}"""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        room = self.room(action)
        sched = self.home.scheduler
        inst = self.home.instances.for_room(room)
        what = str(action.get("alarm", "stop")).lower()
        if what in ("stop", "snooze"):
            rooms = [room] if room in sched.ringers else (inst.rooms or None)
            ringing = [sched.ringers[r] for r in list(sched.ringers) if rooms is None or r in rooms]
            if not ringing and sched.ringers:
                ringing = list(sched.ringers.values())  # "stop" anywhere stops the only alarm
            for r in ringing:
                r.stop()
                sched.ringers.pop(r.room, None)
                if what == "snooze":
                    self.home.instances.snooze(r.inst, r.item, float(action.get("minutes", 9)))
            if what == "snooze" and ringing:
                return self.home.voice.speak("snoozed", room=room, minutes=action.get("minutes", 9))
            return f"stopped {len(ringing)}"
        if what == "set":
            t = clock.parse_hhmm(str(action["at"]))
            due = clock.now().replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if due <= clock.now():
                due += timedelta(days=1)
            self.home.instances.add(inst, "alarm", due, label=str(action.get("label", "")),
                                    repeat=str(action.get("repeat", "none")), room=room)
            return self.home.voice.speak("alarm_set", room=room, time=clock.spoken_time(due))
        raise ValueError("alarm must be stop, snooze or set")


class ModeBackend(_Builtin):
    """{mode: away}  (home, away, night, vacation, guest)"""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        return self.home.state.set_mode(action["mode"], reason=phrase)


class PresenceBackend(_Builtin):
    """{presence: away} for whoever's room it was said in, or {presence: home, person: sam}"""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        person = action.get("person")
        if not person:
            inst = self.home.instances.for_room(self.room(action))
            if inst.main:
                return "presence: who? (say it from your own room or set person:)"
            person = inst.id
        self.home.state.set_presence(str(person), str(action["presence"]), source=phrase or "command")
        return f"{person} {action['presence']}"


class AskBackend(_Builtin):
    """{ask: "Give me a short morning briefing"} -> the AI answers, spoken in the room."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        room = self.room(action)
        ai = self.home.assistant
        if ai is None:
            log.warning("`ask:` step skipped: the AI assistant isn't enabled")
            return "assistant off"
        answer = ai.ask(str(action["ask"]), room=room)
        if action.get("silent"):
            return answer
        self.home.voice.speak(answer, room=room, rooms=action.get("rooms"),
                              everywhere=_on(action.get("everywhere", False)))
        return answer


class PrivacyBackend(_Builtin):
    """{privacy: on, minutes: 60} pauses every mic; add room: here for just this room."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        here = action.get("_context", {}).get("here")
        which = str(action.get("room", "all") or "all").lower()
        room = here if which == "here" else (None if which in ("all", "everywhere") else which)
        on = _on(action["privacy"])
        minutes = float(action["minutes"]) if action.get("minutes") else None
        # say it before going quiet / after waking up
        if on:
            self.home.voice.speak("privacy_on", room=here)
        result = self.home.state.set_privacy(on, minutes, room)
        if not on:
            self.home.voice.speak("privacy_off", room=here)
        return result


class DndBackend(_Builtin):
    """{dnd: on} / {dnd: off}: announcements skip this instance's rooms (alarms still ring)."""

    def run(self, action: dict[str, Any], phrase: str) -> str:
        room = self.room(action)
        inst = self.home.instances.for_room(room)
        on = _on(action["dnd"])
        self.home.instances.set_dnd(inst, on)
        return self.home.voice.speak("dnd_on" if on else "dnd_off", room=room, urgent=True)
