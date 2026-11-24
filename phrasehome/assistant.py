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
        self.location = cfg.get("location", "")
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self._convos: dict[str, tuple[float, list]] = {}
        self._lock = threading.Lock()
        self.tools = self._tools()
        self.system = self._system_prompt()

    # ---------- addressing ----------
    def addressed(self, text: str) -> str | None:
        """If `text` starts with the wake name (optionally "hey"/"ok" first), return the rest."""
        words = normalize(text).split()
        while words and words[0] in ("hey", "ok", "okay", "hi"):
            words = words[1:]
        for name in self.names:
            n = name.split()
            if words[: len(n)] == n:
                return " ".join(words[len(n):])
        return None

    def strip_name(self, text: str) -> str:
        rest = self.addressed(text)
        return text if rest is None else rest

    def vocabulary(self) -> list[str]:
        return sorted({w for n in self.names for w in n.split()} | {"hey", "ok"})

    # ---------- what Claude knows and can do ----------
    def _system_prompt(self) -> str:
        c = self.home.config
        cmds = "\n".join(f"- {cmd.name}: e.g. \"{cmd.phrases[0]}\"" for cmd in c.commands)
        routines = "\n".join(f"- {name}" for name in c.routines) or "(none)"
        slots = "\n".join(f"- {{{k}}}: {', '.join(v)}" for k, v in c.slots.items()) or "(none)"
        doors = ", ".join(c.doors) or "(none)"
        names = ", ".join(self.names)
        return f"""{self.persona}
People address you as: {names}.{f" The home is in {self.location}." if self.location else ""}

How to reply: your reply is spoken aloud by a speaker, so answer in one to three short
sentences of plain speech. Don't use lists, markdown, emoji or URLs. Say numbers and times the
way a person would.

Controlling the home: use the tools. Prefer a routine when one matches the request, otherwise
the matching command. Commands take slot values from these lists:
{slots}
A command whose action uses {{here}} acts on the room the person is speaking from, and the
current room is given with each message. After acting, briefly confirm what you did. If a tool
fails, say so plainly. Don't claim success you didn't get.

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

    def _tools(self) -> list[dict]:
        c = self.home.config
        tools = [
            _strict("run_command", "Run one of the home's voice commands.", {
                "command": {"type": "string", "enum": [cmd.name for cmd in c.commands]},
                "slots": {"type": "array", "description": "Slot values, if the command has slots.",
                          "items": {"type": "object", "additionalProperties": False,
                                    "properties": {"name": {"type": "string"},
                                                   "value": {"type": "string"}},
                                    "required": ["name", "value"]}},
            }, ["command", "slots"]),
            _strict("home_status", "Door states, the people in the face bank, and recent events "
                    "(door scans, commands, locks).", {
                        "event_kind": {"type": "string", "description":
                                       "Only events of this kind (scan, door, command, routine), or empty for all."},
                        "limit": {"type": "integer", "description": "How many recent events, 1-200."},
                    }, ["event_kind", "limit"]),
        ]
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
        if self.web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 3})
        return tools

    def _run_tool(self, name: str, args: dict, room: str | None) -> str:
        c, home = self.home.config, self.home
        if name == "run_command":
            cmd = next(x for x in c.commands if x.name == args["command"])
            values: dict[str, str] = {}
            for s in args.get("slots") or []:
                options = c.slots.get(s["name"])
                if options is None:
                    raise ValueError(f"unknown slot {s['name']}")
                v = s["value"].lower()
                match = options.get(v) or next((val for val in options.values() if val.lower() == v), None)
                if match is None:
                    raise ValueError(f"{s['value']} isn't a valid {s['name']} ({', '.join(options)})")
                values[s["name"]] = match
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
        if name == "home_status":
            limit = max(1, min(200, int(args.get("limit") or 20)))
            bank = []
            try:
                from .faces import FaceBank

                fb = FaceBank(c.resolve_path(c.data_dir) / "faces")
                bank = [{"name": p.name, "allowed": p.allowed, "kind": p.kind,
                         "last_seen": p.last_seen, "times_seen": p.seen} for p in fb.people.values()]
            except Exception:
                pass
            return json.dumps({"doors": home.doors.states(), "face_bank": bank,
                               "events": home.events.query(args.get("event_kind") or None, limit=limit)})
        raise ValueError(f"unknown tool {name}")

    # ---------- the conversation ----------
    def ask(self, question: str, room: str | None = None) -> str:
        with self._lock:
            key = room or "_"
            now = time.monotonic()
            last, messages = self._convos.get(key, (0.0, []))
            if now - last > self.memory_seconds or len(messages) > 40:
                messages = []  # start fresh rather than trimming the middle of a conversation
            doors = ", ".join(f"{d} {s}" for d, s in self.home.doors.states().items()) or "none"
            context = (f"[{time.strftime('%A %d %B %Y, %H:%M')}; speaking from: {room or 'unknown'}; "
                       f"doors: {doors}]")
            messages.append({"role": "user", "content": [{"type": "text", "text": context},
                                                         {"type": "text", "text": question}]})
            answer = self._loop(messages, room)
            self._convos[key] = (time.monotonic(), messages)
            self.home.events.log("assistant", room=room, question=question, answer=answer[:300])
            return answer

    def _loop(self, messages: list, room: str | None) -> str:
        for _ in range(10):
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}],
                tools=self.tools,
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
                    out = self._run_tool(b.name, dict(b.input), room)
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

    def __init__(self, cfg: dict, vosk_model, rate: int) -> None:
        self.kind = cfg.get("transcriber", "vosk")
        self.rate = rate
        self.vosk_model = vosk_model
        if self.kind == "whisper":
            from faster_whisper import WhisperModel

            self.whisper = WhisperModel(cfg.get("whisper_model", "base.en"), device="cpu",
                                        compute_type="int8")

    def __call__(self, pcm: bytes) -> str:
        if self.kind == "whisper":
            import numpy as np

            audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
            segments, _ = self.whisper.transcribe(audio, language="en", beam_size=1)
            return " ".join(s.text.strip() for s in segments)
        import vosk

        rec = vosk.KaldiRecognizer(self.vosk_model, self.rate)
        rec.AcceptWaveform(pcm)
        return json.loads(rec.FinalResult()).get("text", "")


class AssistantRouter:
    """Sits between speech recognition and the command matcher. Decides whether an utterance
    is for the AI, and runs the AI on a background thread so listening never stops."""

    def __init__(self, assistant: Assistant, transcribe: Callable[[bytes], str]) -> None:
        self.assistant = assistant
        self.transcribe = transcribe
        self._follow_up: dict[str, float] = {}

    def handle(self, source: str, room: str | None, text: str, pcm: bytes,
               reply: Callable[[str], None] | None) -> bool:
        """Return True if the AI took this utterance (so it isn't also matched as a command)."""
        now = time.monotonic()
        rest = self.assistant.addressed(text)
        following_up = self._follow_up.get(source, 0) > now
        if rest is None and not following_up:
            return False
        self._follow_up.pop(source, None)
        if reply:
            reply("matched")
        if rest is not None and all(w == "unk" for w in rest.split()):
            # only the name was heard clearly; the question may be in this audio or the next utterance
            full = self.assistant.strip_name(self.transcribe(pcm))
            if not full.strip():
                self._follow_up[source] = now + self.assistant.follow_up_seconds
                self.assistant.home.voice.speak("listening")
                return True
            question = full
        else:
            question = self.assistant.strip_name(self.transcribe(pcm)) if pcm else (rest or text)
        threading.Thread(target=self._answer, args=(question, room, reply), daemon=True).start()
        return True

    def _answer(self, question: str, room: str | None, reply) -> None:
        log.info("assistant%s <- %r", f" [{room}]" if room else "", question)
        try:
            answer = self.assistant.ask(question, room)
            log.info("assistant -> %r", answer)
            self.assistant.home.voice.speak(answer)
            if reply:
                reply("done")
        except Exception as e:
            log.error("assistant failed: %s", e)
            self.assistant.home.voice.speak("assistant_error")
            if reply:
                reply("failed")
