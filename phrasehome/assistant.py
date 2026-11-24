"""The household AI: say its name ("house, ...") and ask anything.

    "house, what's the weather tomorrow?"          -> answers (web search)
    "house, who came to the door today?"           -> reads the event log
    "house, it's movie night, set things up"       -> runs your commands and routines
    "house, is the back door locked?"              -> checks the doors

It is the same set of controls you already configured (commands, routines, doors), handed to
Claude as tools. It can lock doors but can't unlock them unless you set allow_unlock: true.

Privacy: audio never leaves the house. Only what you say after the wake name (as text), plus a
short summary of the house's state, is sent to Anthropic's API.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Callable

from . import clock
from .matcher import normalize

log = logging.getLogger("phrasehome.assistant")

PERSONA = ("You are the voice assistant for this home. You're friendly, calm and brief, "
           "like a good butler.")


def _strict(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required,
                             "additionalProperties": False}}


class Assistant:
    def __init__(self, home, client=None) -> None:
        self.home = home
        cfg = home.config.assistant
        self.names = [normalize(n) for n in cfg.get("names", ["house"])]
        self.model = cfg.get("model", "claude-opus-5-5")
        self.effort = cfg.get("effort", "low")  # quick spoken answers; raise for harder questions
        self.allow_unlock = bool(cfg.get("allow_unlock", False))
        self.web_search = bool(cfg.get("web_search", True))
        self.persona = cfg.get("persona", PERSONA)
        self.memory_seconds = float(cfg.get("memory_minutes", 5)) * 60
        self.follow_up_seconds = float(cfg.get("follow_up_seconds", 8))
        self.continuous = bool(cfg.get("continuous", True))  # listen for a follow-up after answering
        self.location = cfg.get("location", "")
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self._convos: dict[str, tuple[float, list]] = {}
        self._lock = threading.Lock()
        self._profiles: dict[str, tuple[str, list]] = {}

    # ---------- addressing ----------
    def names_for(self, room: str | None) -> list[str]:
        names = list(self.names)
        inst = self.home.instances.for_room(room)
        names += [normalize(w) for w in inst.wake_names]
        return names

    def addressed(self, text: str, room: str | None = None) -> str | None:
        """If `text` starts with a wake name for this room (optionally "hey"/"ok" first),
        return the rest."""
        words = normalize(text).split()
        while words and words[0] in ("hey", "ok", "okay", "hi"):
            words = words[1:]
        for name in sorted(self.names_for(room), key=len, reverse=True):
            n = name.split()
            if words[: len(n)] == n:
                return " ".join(words[len(n):])
        return None

    def strip_name(self, text: str, room: str | None = None) -> str:
        rest = self.addressed(text, room)
        return text if rest is None else rest

    def vocabulary(self) -> list[str]:
        words = {w for n in self.names for w in n.split()}
        for inst in self.home.instances.all.values():
            words |= {w for n in inst.wake_names for w in normalize(n).split()}
        return sorted(words | {"hey", "ok"})

    # ---------- what Claude knows and can do, per instance ----------
    def profile(self, inst) -> tuple[str, list]:
        if inst.id not in self._profiles:
            self._profiles[inst.id] = (self._system_prompt(inst), self._tools(inst))
        return self._profiles[inst.id]

    def _system_prompt(self, inst) -> str:
        c = self.home.config
        cmds = "\n".join(f"- {cmd.name}: e.g. \"{cmd.phrases[0]}\"" for cmd in c.commands)
        routines = "\n".join(f"- {name}" for name in c.routines) or "(none)"
        slots = "\n".join(f"- {{{k}}}: {', '.join(list(v)[:25])}" for k, v in c.slots.items()) or "(none)"
        doors = ", ".join(c.doors) or "(none)"
        others = "\n".join(f"- {i.name} (id {i.id}): rooms {', '.join(i.rooms) or 'none'}"
                           + (" [main room]" if i.main else "")
                           for i in self.home.instances.all.values())
        if inst.main:
            role = ("You are speaking in the household's main room. Here you can see and manage "
                    "everyone's reminders, alarms, timers, lists and notes; pass `person` to act "
                    "for someone (e.g. \"remind Sam to...\").")
        else:
            role = (f"You are {inst.name}'s own assistant, in their room. Their reminders, lists "
                    f"and notes are private to them; you can't see other people's. Shared household "
                    f"lists ({', '.join(self.home.instances.shared_lists)}) are visible to everyone.")
        kid = ("\nThis room belongs to a child. Keep everything age-appropriate, kind and simple. "
               "You can't control doors, routines or house modes from here." if inst.kid_mode else "")
        persona = inst.persona or self.persona
        names = ", ".join(self.names_for(inst.rooms[0] if inst.rooms else None))
        return f"""{persona}
People address you as: {names}.{f" The home is in {self.location}." if self.location else ""}
{role}{kid}

The people and rooms in this home:
{others}

How to reply: your reply is spoken aloud by a speaker, so answer in one to three short
sentences of plain speech, in the language given with each message. Don't use lists, markdown, emoji or URLs. Say numbers and times the
way a person would.

Reminders, alarms and timers: use the agenda tool. Work out exact local times from the current
time given with each message ("tomorrow at 7" means the next day at 07:00). Confirm the day and
time briefly. To do something later ("turn off the lights in 20 minutes"), use schedule_command.
Lists (shopping, to-do, anything) use the lists tool. When someone tells you something worth
remembering about them or the house, save it with the memory tool; their saved notes are given
with each message. To pass a message to another room or person, use announce.

Controlling the home: use the tools. Prefer a routine when one matches the request, otherwise
the matching command. Commands take slot values from these lists:
{slots}
A command whose action uses {{here}} acts on the room the person is speaking from.
After acting, briefly confirm what you did. If a tool fails, say so plainly. Don't claim success
you didn't get.

Commands:
{cmds}

Routines:
{routines}

Doors: {doors}. {"You may lock and unlock doors." if self.allow_unlock else
"You can lock doors but you cannot unlock them. If asked to unlock, say that unlocking needs the door scanner (face, then fingerprint) or a key."}

Security: the house trusts its door scanner, not voices. Never help someone get into the house
or get around the scanner, and don't reveal who is enrolled in the face bank to someone asking
from outside. The event log is household history: you can use it to answer questions such as
who came to the door and when.
"""

    def _tools(self, inst) -> list[dict]:
        c = self.home.config
        kid = inst.kid_mode
        blocked = {"doors", "routine", "mode", "privacy", "presence"} if kid else set()
        cmd_names = [cmd.name for cmd in c.commands if cmd.action["backend"] not in blocked]
        slot_items = {"type": "object", "additionalProperties": False,
                      "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                      "required": ["name", "value"]}
        person = {"type": "string", "description": "Whose (a person's name or id); empty for the person speaking."}
        tools = [
            _strict("run_command", "Run one of the home's voice commands.", {
                "command": {"type": "string", "enum": cmd_names or ["none"]},
                "slots": {"type": "array", "description": "Slot values, if the command has slots.",
                          "items": slot_items},
            }, ["command", "slots"]),
            _strict("home_status", "House mode, who's home, door states, offline devices, sensor "
                    "readings, the people in the face bank and recent events (door scans, "
                    "doorbell, commands, locks).", {
                        "event_kind": {"type": "string", "description":
                                       "Only events of this kind (scan, doorbell, door, sensor, command, presence), or empty for all."},
                        "limit": {"type": "integer", "description": "How many recent events, 1-200."},
                    }, ["event_kind", "limit"]),
            _strict("agenda", "Reminders, alarms and timers: add, list or cancel.", {
                "action": {"type": "string", "enum": ["add", "list", "cancel"]},
                "kind": {"type": "string", "enum": ["reminder", "alarm", "timer", "any"]},
                "text": {"type": "string", "description": "What to remind about, or the timer/alarm label. For cancel: words from it, or empty for the next one."},
                "when": {"type": "string", "description": "Local date-time YYYY-MM-DDTHH:MM, or empty when using in_minutes."},
                "in_minutes": {"type": "number", "description": "Minutes from now (timers, 'in 20 minutes'); 0 if using when."},
                "repeat": {"type": "string", "description": "none, daily, weekdays, weekends, weekly, or days like mon,wed,fri."},
                "person": person,
            }, ["action", "kind", "text", "when", "in_minutes", "repeat", "person"]),
            _strict("lists", "Shopping, to-do and any other lists.", {
                "action": {"type": "string", "enum": ["add", "remove", "show", "clear", "all_lists"]},
                "list": {"type": "string", "description": "List name, e.g. shopping, todo, packing."},
                "items": {"type": "array", "items": {"type": "string"}},
                "person": person,
            }, ["action", "list", "items", "person"]),
            _strict("memory", "Save or delete a note about a person or the house.", {
                "action": {"type": "string", "enum": ["remember", "forget"]},
                "text": {"type": "string"},
                "person": person,
            }, ["action", "text", "person"]),
            _strict("announce", "Say something out loud in other rooms (intercom).", {
                "text": {"type": "string", "description": "Exactly what to say."},
                "rooms": {"type": "array", "items": {"type": "string"}, "description": "Room names, or empty."},
                "person": {"type": "string", "description": "Say it in this person's rooms, or empty."},
                "everyone": {"type": "boolean", "description": "Say it in every room."},
            }, ["text", "rooms", "person", "everyone"]),
        ]
        if not kid:
            tools.append(_strict("schedule_command", "Run a command later.", {
                "command": {"type": "string", "enum": cmd_names or ["none"]},
                "slots": {"type": "array", "items": slot_items},
                "when": {"type": "string", "description": "Local date-time YYYY-MM-DDTHH:MM, or empty."},
                "in_minutes": {"type": "number", "description": "Minutes from now, or 0 if using when."},
            }, ["command", "slots", "when", "in_minutes"]))
            from .state import MODES

            tools.append(_strict("set_mode", "Set the house mode.", {
                "mode": {"type": "string", "enum": MODES}}, ["mode"]))
            if c.routines:
                tools.append(_strict("run_routine", "Run a routine (several actions at once).",
                                     {"routine": {"type": "string", "enum": list(c.routines)}},
                                     ["routine"]))
            if c.doors:
                states = ["locked", "unlocked"] if self.allow_unlock else ["locked"]
                tools.append(_strict("set_door", "Lock (or, if allowed, unlock) a door.", {
                    "door": {"type": "string", "enum": list(c.doors) + ["all"]},
                    "state": {"type": "string", "enum": states},
                }, ["door", "state"]))
        if any(i.calendar for i in self.home.instances.all.values()):
            tools.append(_strict("calendar", "Events from someone's calendar.", {
                "person": person,
                "from_day": {"type": "integer", "description": "0 = today, 1 = tomorrow..."},
                "days": {"type": "integer", "description": "How many days to include, 1-31."},
            }, ["person", "from_day", "days"]))
        if self.web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 3})
        return tools

    # ---------- running tools ----------
    def _slot_values(self, args: dict) -> dict[str, str]:
        c = self.home.config
        values: dict[str, str] = {}
        for s in args.get("slots") or []:
            options = c.slots.get(s["name"])
            if options is None:
                raise ValueError(f"unknown slot {s['name']}")
            v = s["value"].lower()
            match = options.get(v) or next((val for val in options.values() if val.lower() == v), None)
            if match is None:
                raise ValueError(f"{s['value']} isn't a valid {s['name']} ({', '.join(list(options)[:15])})")
            values[s["name"]] = match
        return values

    def _when(self, args: dict):
        from datetime import datetime, timedelta

        if args.get("in_minutes"):
            return clock.now() + timedelta(minutes=float(args["in_minutes"]))
        if args.get("when"):
            t = datetime.fromisoformat(args["when"])
            return t if t.tzinfo else t.replace(tzinfo=clock.now().tzinfo)
        raise ValueError("give `when` or `in_minutes`")

    def _run_tool(self, name: str, args: dict, room: str | None, inst=None) -> str:
        c, home = self.home.config, self.home
        inst = inst or home.instances.for_room(room)
        insts = home.instances
        if name == "run_command":
            cmd = next(x for x in c.commands if x.name == args["command"])
            values = self._slot_values(args)
            if room:
                values.setdefault("here", room)
            result = home.run_action(cmd.action, values, phrase=f"assistant: {cmd.name}")
            home.events.log("command", name=cmd.name, room=room, via="assistant")
            return result or "done"
        if name == "run_routine":
            return home.run_action({"backend": "routine", "routine": args["routine"]},
                                   {"here": room} if room else {})
        if name == "set_door":
            if args["state"] == "unlocked" and not self.allow_unlock:
                raise PermissionError("unlocking by voice is turned off")
            return home.doors.set(args["door"], args["state"], reason="assistant")
        if name == "set_mode":
            return home.state.set_mode(args["mode"], reason="assistant")
        if name == "home_status":
            limit = max(1, min(200, int(args.get("limit") or 20)))
            bank = []
            try:
                from .faces import FaceBank

                fb = FaceBank(c.resolve_path(c.data_dir) / "faces")
                bank = [{"name": p.name, "allowed": p.is_allowed(), "kind": p.kind,
                         "last_seen": p.last_seen, "times_seen": p.seen} for p in fb.people.values()]
            except Exception:
                pass
            sensors = {}
            if home.sensors:
                sensors = {f"{n}/{i}": {k: v for k, v in val.items() if k in ("state", "value")}
                           for (n, i), val in list(home.sensors.values.items())[:60]}
            return json.dumps({
                "mode": home.state.mode, "people_home": home.state.home_people(),
                "doors": home.doors.states(),
                "offline_devices": sorted(home.sensors.offline) if home.sensors else [],
                "sensors": sensors, "face_bank": bank,
                "events": home.events.query(args.get("event_kind") or None, limit=limit)})
        if name == "agenda":
            action, kind = args["action"], args.get("kind") or "any"
            if action == "list" and inst.main and str(args.get("person", "")).lower() in ("everyone", "all"):
                out = {i.name: insts.items(i) for i in insts.all.values()}
                return json.dumps(out)
            target = insts.get(args.get("person"), asker=inst)
            kinds = None if kind == "any" else (kind,)
            if action == "add":
                if kind == "any":
                    kind = "reminder"
                due = self._when(args)
                where = room if (target.id == inst.id and room) else (target.rooms[0] if target.rooms else room)
                text = args.get("text", "")
                item = insts.add(target, kind, due, text=text if kind == "reminder" else "",
                                 label=text if kind != "reminder" else "", repeat=args.get("repeat") or "none",
                                 room=where)
                return f"added {kind} {item['id']} for {target.name} at {item['due']} in {where or 'the main room'}"
            if action == "list":
                return json.dumps(insts.items(target, kinds))
            if action == "cancel":
                hit = insts.cancel(target, args.get("text", ""), kinds)
                if hit:
                    for r in list(home.scheduler.ringers.values()):
                        if r.item.get("id") in {h["id"] for h in hit}:
                            r.stop()
                return f"cancelled {len(hit)}: " + ", ".join(h["text"] or h["label"] or h["kind"] for h in hit)
        if name == "lists":
            action = args["action"]
            target = insts.get(args.get("person"), asker=inst)
            lname = args.get("list") or "todo"
            if action == "all_lists":
                return json.dumps({n: insts.list_get(target, n) for n in insts.list_names(target)})
            if action == "add":
                return json.dumps(insts.list_add(target, lname, args.get("items") or []))
            if action == "remove":
                return json.dumps(insts.list_remove(target, lname, args.get("items") or []))
            if action == "show":
                return json.dumps(insts.list_get(target, lname))
            if action == "clear":
                insts.list_clear(target, lname)
                return "cleared"
        if name == "memory":
            target = insts.get(args.get("person"), asker=inst)
            if args["action"] == "remember":
                insts.remember(target, args["text"])
                return "saved"
            return f"forgot {insts.forget(target, args['text'])} notes"
        if name == "announce":
            rooms = [r.lower() for r in args.get("rooms") or []]
            if args.get("person"):
                target = insts.get(args["person"], asker=insts.main)  # anyone may message anyone
                rooms += target.rooms
            everywhere = bool(args.get("everyone"))
            if not rooms and not everywhere:
                raise ValueError("say who or which rooms")
            home.voice.speak("intercom", rooms=rooms or None, everywhere=everywhere,
                             text=args["text"])
            home.events.log("intercom", room=room, to=",".join(rooms) or "everyone", text=args["text"])
            return f"said it in {', '.join(rooms) if rooms else 'every room'}"
        if name == "schedule_command":
            cmd = next(x for x in c.commands if x.name == args["command"])
            values = self._slot_values(args)
            if room:
                values["here"] = room
            action = home.render(cmd.action, values)
            item = insts.add(inst, "action", self._when(args), text=cmd.name, room=room, action=action)
            return f"scheduled {cmd.name} for {item['due']}"
        if name == "calendar":
            from .calendar_feed import upcoming

            target = insts.get(args.get("person"), asker=inst)
            if not target.calendar:
                return f"{target.name} has no calendar connected"
            days = max(1, min(31, int(args.get("days") or 1)))
            return json.dumps(upcoming(target.calendar, days, int(args.get("from_day") or 0)))
        raise ValueError(f"unknown tool {name}")

    # ---------- the conversation ----------
    def context(self, inst, room: str | None, person: str | None = None) -> str:
        h = self.home
        lang = h.language_for(room) if hasattr(h, "language_for") else "en"
        who = f" speaker recognized by voice: {inst.name};" if person else ""
        doors = ", ".join(f"{d} {s}" for d, s in h.doors.states().items()) or "none"
        upcoming = [f"{i['kind']} {i['due'][5:16].replace('T', ' ')} {i['text'] or i['label']}".strip()
                    for i in h.instances.items(inst)[:6]]
        notes = [m["text"] for m in h.instances.memories(inst)][-30:]
        return (f"[{clock.now().strftime('%A %d %B %Y, %H:%M %Z')}; speaking from: {room or 'unknown'}"
                f" ({inst.name}'s {'main room' if inst.main else 'room'});{who} language: {lang};"
                f" house mode: {h.state.mode};"
                f" home now: {', '.join(h.state.home_people()) or 'unknown'}; doors: {doors};"
                f" {inst.name}'s upcoming: {'; '.join(upcoming) or 'nothing'};"
                f" {inst.name}'s saved notes: {' | '.join(notes) or 'none'}]")

    def instance_for(self, room: str | None, person: str | None = None):
        """The speaker's own instance when their voice was recognized, else the room's."""
        if person and person in self.home.instances.all:
            return self.home.instances.all[person]
        return self.home.instances.for_room(room)

    def ask(self, question: str, room: str | None = None, person: str | None = None) -> str:
        inst = self.instance_for(room, person)
        system, tools = self.profile(inst)
        with self._lock:
            key = room or "_"
            now = time.monotonic()
            last, messages = self._convos.get(key, (0.0, []))
            if now - last > self.memory_seconds or len(messages) > 40:
                messages = []  # start fresh rather than trimming the middle of a conversation
            messages.append({"role": "user", "content": [
                {"type": "text", "text": self.context(inst, room, person)},
                {"type": "text", "text": question}]})
            answer = self._loop(messages, room, inst, system, tools)
            self._convos[key] = (time.monotonic(), messages)
            self.home.events.log("assistant", room=room, instance=inst.id, question=question,
                                 answer=answer[:300])
            return answer

    def _loop(self, messages: list, room: str | None, inst, system: str, tools: list) -> str:
        for _ in range(10):
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                return "Sorry, I can't help with that."
            if resp.stop_reason == "pause_turn":
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                return spoken(" ".join(b.text for b in resp.content if b.type == "text"))
            results = []
            for b in calls:
                try:
                    out = self._run_tool(b.name, dict(b.input), room, inst)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": str(out)})
                except Exception as e:
                    log.warning("assistant tool %s failed: %s", b.name, e)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": f"Error: {e}", "is_error": True})
            messages.append({"role": "user", "content": results})
        return "Sorry, that took too many steps. Try asking a simpler way."


def spoken(text: str) -> str:
    """Strip anything that sounds bad read aloud."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # markdown links -> their text
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"[*_#`>]+", "", text)
    return re.sub(r"\s+", " ", text).strip() or "Done."


class Transcriber:
    """Turns a whole utterance into free text (the command recognizer only knows your phrases).
    vosk: uses the same offline model. whisper: more accurate, needs `pip install faster-whisper`."""

    def __init__(self, cfg: dict, vosk_model, rate: int, models: dict | None = None,
                 main_lang: str = "en") -> None:
        self.kind = cfg.get("transcriber", "vosk")
        self.rate = rate
        self.vosk_model = vosk_model
        self.models = models or {}
        self.main_lang = main_lang
        if self.kind == "whisper":
            from faster_whisper import WhisperModel

            self.whisper = WhisperModel(cfg.get("whisper_model", "base.en"), device="cpu",
                                        compute_type="int8")

    def __call__(self, pcm: bytes, lang: str | None = None) -> str:
        if self.kind == "whisper":
            import numpy as np

            audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
            segments, _ = self.whisper.transcribe(audio, language=lang or self.main_lang, beam_size=1)
            return " ".join(s.text.strip() for s in segments)
        import vosk

        rec = vosk.KaldiRecognizer(self.models.get(lang, self.vosk_model), self.rate)
        rec.AcceptWaveform(pcm)
        return json.loads(rec.FinalResult()).get("text", "")


class AssistantRouter:
    """Sits between speech recognition and the command matcher. Decides whether an utterance
    is for the AI, and runs the AI on a background thread so listening never stops."""

    def __init__(self, assistant: Assistant, transcribe: Callable[[bytes], str]) -> None:
        self.assistant = assistant
        self.transcribe = transcribe
        self._follow_up: dict[str, float] = {}

    def _transcribe(self, pcm: bytes, lang: str | None) -> str:
        try:
            return self.transcribe(pcm, lang)
        except TypeError:  # a simple one-argument transcriber
            return self.transcribe(pcm)

    def handle(self, source: str, room: str | None, text: str, pcm: bytes,
               reply: Callable[[str], None] | None, person: str | None = None,
               lang: str | None = None) -> bool:
        """Return True if the AI took this utterance (so it isn't also matched as a command)."""
        now = time.monotonic()
        rest = self.assistant.addressed(text, room)
        following_up = self._follow_up.get(source, 0) > now
        if rest is None and not following_up:
            return False
        self._follow_up.pop(source, None)
        if reply:
            reply("matched")
        if rest is not None and all(w == "unk" for w in rest.split()):
            # only the name was heard clearly; the question may be in this audio or the next utterance
            full = self.assistant.strip_name(self._transcribe(pcm, lang), room)
            if not full.strip():
                self._follow_up[source] = now + self.assistant.follow_up_seconds
                self.assistant.home.voice.speak("listening", room=room)
                return True
            question = full
        else:
            question = self.assistant.strip_name(self._transcribe(pcm, lang), room) if pcm else (rest or text)
            if not question.strip() or all(w == "unk" for w in question.split()):
                return True  # nothing usable was said
        threading.Thread(target=self._answer, args=(question, room, reply, person, source),
                         daemon=True).start()
        return True

    def _answer(self, question: str, room: str | None, reply, person: str | None = None,
                source: str | None = None) -> None:
        log.info("assistant%s <- %r", f" [{room}]" if room else "", question)
        try:
            answer = self.assistant.ask(question, room, person=person) if person else \
                self.assistant.ask(question, room)
            log.info("assistant -> %r", answer)
            self.assistant.home.voice.speak(answer, room=room)
            if reply:
                reply("done")
            if self.assistant.continuous and source:
                # continuous conversation: a follow-up doesn't need the wake name
                self._follow_up[source] = time.monotonic() + self.assistant.follow_up_seconds
        except Exception as e:
            log.error("assistant failed: %s", e)
            self.assistant.home.voice.speak("assistant_error", room=room)
            if reply:
                reply("failed")
