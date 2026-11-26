"""Understanding what was meant, not just what was heard.

History      everything heard, what it matched, what the AI was asked and answered (data/
             history.jsonl). Kept only while hearing logs are on (settings.log_heard, on during
             `simulate`) or `history: {enabled: true}`; `history: {days: 14}` how long.
Aliases      "this is what I meant": a misheard sentence taught to mean a command, from the
             History page (data/aliases.json). The matcher treats it like one of the command's phrases.
did_you_mean when what was heard is close to some commands but not close enough, the AI picks
             the one you most likely meant (or none).
Confirmations
             risky commands (locks, the alarm, freak mode, away/vacation, lockdown, panic, fire,
             leaving) ask "Did you say ...?" when the house isn't sure. Yes runs it (and remembers
             the wording), no cancels. Fire goes ahead if nobody answers.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.understanding")

YES = {"yes", "yeah", "yep", "yup", "correct", "do it", "yes please", "affirmative", "sure", "go ahead", "right"}
NO = {"no", "nope", "nah", "cancel", "don't", "dont", "stop", "never mind", "no thanks", "wrong"}
CONFIRM_WORDS = sorted({w for p in YES | NO for w in p.split()})

RISKY_BACKENDS = {"doors", "security", "freak"}
RISKY_ROUTINES = {"lockdown", "panic", "fire_mode", "leaving"}
GO_AHEAD_IF_NO_ANSWER = {"fire_mode"}  # someone yelling "fire" may not be able to answer


# ---------- history ----------
class History:
    def __init__(self, home) -> None:
        self.home = home
        cfg = home.config.extra.get("history") or {}
        self.enabled = bool(cfg.get("enabled", home.config.settings.log_heard))
        self.days = float(cfg.get("days", 14))
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "history.jsonl"
        self._lock = threading.Lock()

    def add(self, **entry: Any) -> dict | None:
        if not self.enabled:
            return None
        e = {"id": uuid.uuid4().hex[:8], "time": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(e, default=str) + "\n")
        self.home.events.live("history", id=e["id"])  # the History page refreshes
        return e

    def entries(self, query: str = "", limit: int = 300) -> list[dict]:
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - self.days * 86400))
        out: list[dict] = []
        with self._lock:
            try:
                lines = self.path.read_text().splitlines()
            except OSError:
                return []
        q = query.lower().strip()
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("time", "") < cutoff:
                break
            if q and q not in json.dumps(e).lower():
                continue
            out.append(e)
            if len(out) >= limit:
                break
        return out

    def get(self, entry_id: str) -> dict | None:
        return next((e for e in self.entries(limit=100000) if e.get("id") == entry_id), None)

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)


# ---------- aliases ----------
class Aliases:
    def __init__(self, config) -> None:
        self.config = config
        self.path = Path(config.resolve_path(config.data_dir)) / "aliases.json"

    def all(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []

    def add(self, heard: str, command: str, slots: dict | None = None) -> dict:
        from .matcher import normalize

        heard = normalize(heard)
        if not heard:
            raise ValueError("nothing to teach")
        if not any(c.name == command for c in self.config.commands):
            raise ValueError(f"no command called {command!r}")
        items = [a for a in self.all() if a["heard"] != heard]
        item = {"heard": heard, "command": command, "slots": dict(slots or {}),
                "added": time.strftime("%Y-%m-%d %H:%M:%S")}
        items.append(item)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, indent=1))
        return item

    def remove(self, heard: str) -> bool:
        from .matcher import normalize

        items = self.all()
        keep = [a for a in items if a["heard"] != normalize(heard)]
        self.path.write_text(json.dumps(keep, indent=1))
        return len(keep) < len(items)

    def candidates(self) -> list:
        """For the matcher: each taught sentence is one more phrase of its command."""
        from .matcher import Candidate

        cmds = {c.name: c for c in self.config.commands}
        return [Candidate(a["heard"], cmds[a["command"]], dict(a.get("slots") or {}))
                for a in self.all() if a.get("command") in cmds]


def candidates_with_aliases(config, lang: str | None = None) -> list:
    from .matcher import expand

    s = config.settings
    english = str(lang or s.language or "en").lower().startswith("en")
    return (expand(config.commands, config.slots, lang, variants=bool(getattr(s, "phrase_variants", True))
                   and english) + Aliases(config).candidates())


# ---------- risky commands ----------
def is_risky(cmd, config=None) -> bool:
    names = set(getattr(getattr(config, "settings", None), "confirm_commands", None) or [])
    if cmd.name in names:
        return True
    a = cmd.action
    b = a.get("backend")
    if b in RISKY_BACKENDS:
        return not (b == "freak" and str(a.get("freak")).lower() in ("off", "false"))  # ending it is harmless
    if b == "routine" and str(a.get("routine")) in RISKY_ROUTINES:
        return True
    return b == "mode" and str(a.get("mode")).lower() in ("away", "vacation")


def is_sure(m) -> bool:
    return m.score >= 92 and (m.confidence or 1.0) >= 0.8 and getattr(m, "via", "voice") == "voice"


class Confirmations:
    """'Did you say lockdown?' - one waiting question per room."""

    def __init__(self, home, window: float = 8.0) -> None:
        self.home = home
        self.window = window
        self.pending: dict[str | None, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def ask(self, m) -> None:
        with self._lock:
            self.pending[m.room] = (time.monotonic() + self.window, m)
        what = m.command.phrases[0]
        self.home.events.live("confirming", room=m.room, command=m.command.name, what=what)
        self.home.voice.speak("confirm_ask", room=m.room, what=what, urgent=True)
        self.home.timeline.call_in(self.window, lambda room=m.room, mm=m: self._timeout(room, mm))

    def _timeout(self, room, m) -> None:
        with self._lock:
            cur = self.pending.get(room)
            if not cur or cur[1] is not m:
                return
            del self.pending[room]
        if self._target(m) in GO_AHEAD_IF_NO_ANSWER:
            log.info("no answer to 'did you say %s?': going ahead (safety)", m.command.phrases[0])
            self._run(m, "no answer (safety)")
        else:
            self._record(m, "no answer")

    @staticmethod
    def _target(m) -> str:
        return str(m.command.action.get("routine") or m.command.name)

    def answer(self, room: str | None, text: str) -> bool:
        """Called with the next thing heard in that room. True if it was the yes/no."""
        from .matcher import normalize

        with self._lock:
            cur = self.pending.get(room)
            if not cur:
                return False
            deadline, m = cur
            if time.monotonic() > deadline:
                return False
            said = _without_name(self.home, normalize(text))  # "house, yes" / "yes house"
            yes = said in YES or any(said.startswith(y + " ") for y in YES)
            no = said in NO or any(said.startswith(n + " ") for n in NO)
            if not (yes or no):
                del self.pending[room]  # they said something else: drop the question
                return False
            del self.pending[room]
        if yes:
            if m.heard and getattr(m, "via", "voice") != "typed":
                try:
                    Aliases(self.home.config).add(m.heard, m.command.name, m.slots)  # remember the wording
                    engine = getattr(self.home, "engine", None)
                    if engine is not None:
                        engine.reload()
                except ValueError:
                    pass
            self._run(m, "yes")
        else:
            self.home.voice.speak("confirm_cancelled", room=room)
            self._record(m, "no")
        return True

    def _run(self, m, why: str) -> None:
        m.confirmed = True
        self._record(m, why)
        self.home.submit(m)

    def _record(self, m, answer: str) -> None:
        h = getattr(self.home, "history", None)
        if h:
            h.add(kind="confirm", room=m.room, heard=m.heard, matched=m.command.name, answer=answer)


def _without_name(home, said: str) -> str:
    """Drop the house's name (and "hey") from the start or end of a short answer."""
    a = getattr(home, "assistant", None)
    names = {w for n in (getattr(a, "names", None) or ["house", "home"]) for w in n.split()} | {"hey", "ok", "okay"}
    words = said.split()
    while len(words) > 1 and words[0] in names:
        words = words[1:]
    while len(words) > 1 and words[-1] in names:
        words = words[:-1]
    return " ".join(words)


# ---------- did you mean ----------
def did_you_mean(home, heard: str, near: list) -> Any:
    """near: [(score, candidate)] that almost matched. Returns the candidate the AI picked, or None."""
    if not near:
        return None
    from . import ai

    options = {}
    for score, cand in near:
        options.setdefault(cand.command.name, cand)
    lines = "\n".join(f"- {name}: e.g. \"{c.text}\"" for name, c in options.items())
    prompt = (f"A voice assistant's speech recognition heard: \"{heard}\"\nIt may be misheard. Which of these "
              f"commands did the person most likely say? Answer \"none\" unless you're fairly sure.\n{lines}")
    try:
        out = ai.ask(home, prompt, schema=ai.obj({"command": {"type": "string",
                                                              "enum": list(options) + ["none"]}}),
                     system="You map misheard voice commands to the command that was meant. Be strict.")
    except Exception as e:
        log.info("did-you-mean unavailable (%s); taking the closest if it's close", e)
        best = max(near, key=lambda x: x[0])
        return best[1] if best[0] >= 78 else None
    pick = (out or {}).get("command")
    return options.get(pick) if pick and pick != "none" else None


# ---------- was that for the house? ----------
INTENT_SYSTEM = ("You decide whether something said to a home voice assistant needs it to act or answer. "
                 "People often say the assistant's name in normal conversation without meaning it. Be strict: "
                 "only a real request or question counts.")


def intent(home, said: str, name: str = "house") -> str:
    """What `said` (the words after the house's name) is: 'task', 'question' or 'ignore'.
    Obvious ones are decided from the word bank straight away; the rest by the AI's quick model.
    If the AI can't be reached, it counts as a question (the house answers, as before)."""
    from .attention import examples_for
    from .wordbank import quick_intent

    learned = examples_for(home)
    known = learned.lookup(said)
    if known is not None:  # this home has told the house about this exact sentence before
        return "question" if known else "ignore"
    quick = quick_intent(said)
    if quick:
        return quick
    from . import ai

    prompt = (f"Speech recognition heard someone say the assistant's name, \"{name}\", followed by:\n\"{said}\"\n"
              "It may be slightly misheard. What did they mean?\n"
              "- task: they want the house to do something (lights, doors, music, a timer, a reminder, a list, "
              "remember something...). Implied requests count, like \"it's freezing in here\" or \"we're out of milk\".\n"
              "- question: they want the house to tell them something, or they're talking to it and expect a reply.\n"
              f"- neither: \"{name}\" was just a word in a sentence not meant for the assistant (\"house party was "
              "fun\", \"home sweet home\", \"house looks great today\"), thanks, a remark, or anything that needs no reply."
              + learned.prompt())
    try:
        out = ai.ask(home, prompt, system=INTENT_SYSTEM, quick=True, max_tokens=512,
                     schema=ai.obj({"kind": {"type": "string", "enum": ["task", "question", "neither"]}}))
    except Exception as e:
        log.info("couldn't check whether that was for the house (%s); answering it", e)
        return "question"
    kind = (out or {}).get("kind")
    return kind if kind in ("task", "question") else "ignore"
