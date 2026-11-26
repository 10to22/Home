"""The AI, smarter and cheaper:

Cost meter      every AI call is counted (data/ai_usage.jsonl): tokens, cost and how long it took, per day,
                per person and per room. Dashboard /ai, or `phrasehome ai-usage`.
Answer cache    the same question within a few minutes ("what's the weather?") is answered without a new
                AI call. Never for things that change the house. ai_extras: {cache_minutes: 3}
Offline         when the internet is down, a local model answers (ai_extras: {offline: {url: ..., model: ...}},
                an Ollama on the Pi or a laptop). Commands never needed the internet.
Morning context today's weather, the next calendar events and alarms go with every question.
Styles          "too long" / "shorter" / "more detail" set how much each person gets, and it sticks.
Homework        a tutor, not an answer machine: steps, hints, and even advanced calculus broken into
                tiny, silly, easy pieces. Kids' rooms, people in `homework: {people: [...]}`, or "help me
                with my homework".
Recipes         "house, let's make banana bread": then "next step", "go back", "repeat that",
                "ingredients" without the name, while cooking.
Facts           the household's facts: bin day, the plumber, the wifi password (secret ones only in the
                main room, never in kids' rooms, never written to logs).
Why             "why did the lights come on?": explains from the event log.
Weekly summary  Sunday evening: energy, security events and birthdays coming up.
Proactive       the watcher speaks up at most once an hour (watcher: {max_per_hour: 1}).

    ai_extras:
      cache_minutes: 3
      offline: { url: http://localhost:11434, model: qwen3:4b }
      weekly: { day: sun, time: "18:00" }
    facts:
      bin day: Thursday night, recycling every other week
      plumber: Joe, 555 0100
      wifi password: { value: hunter2, secret: true }
      birthdays: { Sam: "03-14", Ana: "11-30" }
    homework:
      people: [sam]
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.ai")

# $ per million tokens (input, output). Cache reads cost 10% of input, cache writes 125%.
PRICES = {
    "claude-fable-5-2": (8.0, 40.0), "claude-fable-5-1": (10.0, 50.0), "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50), "claude-haiku-4-5": (1.0, 5.0),
}
WEB_SEARCH_PER_1000 = 10.0

_ctx = threading.local()


def _cfg(home) -> dict:
    return dict(home.config.extra.get("ai_extras") or {})


def data_dir(home) -> Path:
    return Path(home.config.resolve_path(home.config.data_dir))


def asking(room: str | None, person: str | None) -> None:
    """Who the AI calls on this thread are for (the cost meter)."""
    _ctx.room, _ctx.person = room, person
    _ctx.tools = []


def note_tools(names: list[str]) -> None:
    getattr(_ctx, "tools", []).extend(names)


def tools_used() -> list[str]:
    return list(getattr(_ctx, "tools", []))


# ---------- the cost meter, and the offline fallback ----------
def cost(model: str, usage) -> float:
    if usage is None:
        return 0.0
    inp, out = PRICES.get(model, (0.0, 0.0))
    g = (lambda k: float(getattr(usage, k, 0) or 0)) if not isinstance(usage, dict) else \
        (lambda k: float(usage.get(k) or 0))
    total = (g("input_tokens") * inp + g("output_tokens") * out + g("cache_read_input_tokens") * inp * 0.1
             + g("cache_creation_input_tokens") * inp * 1.25) / 1e6
    stu = getattr(usage, "server_tool_use", None) if not isinstance(usage, dict) else usage.get("server_tool_use")
    searches = float(getattr(stu, "web_search_requests", 0) or 0) if stu is not None else 0.0
    return total + searches * WEB_SEARCH_PER_1000 / 1000


class Meter:
    def __init__(self, home) -> None:
        self.home = home
        self.path = data_dir(home) / "ai_usage.jsonl"
        self._lock = threading.Lock()

    def record(self, model: str, usage, seconds: float, kind: str = "answer", offline: bool = False) -> dict:
        g = (lambda k: int(getattr(usage, k, 0) or 0)) if usage is not None else (lambda k: 0)
        e = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "kind": kind,
             "room": getattr(_ctx, "room", None), "person": getattr(_ctx, "person", None),
             "in": g("input_tokens"), "out": g("output_tokens"), "cache_read": g("cache_read_input_tokens"),
             "cache_write": g("cache_creation_input_tokens"), "seconds": round(seconds, 2),
             "cost": round(0.0 if offline else cost(model, usage), 6), "offline": offline}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(e) + "\n")
        return e

    def entries(self, days: int = 31) -> list[dict]:
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("time", "") >= cutoff:
                out.append(e)
        return out

    def summary(self, days: int = 31) -> dict:
        es = self.entries(days)
        by: dict[str, dict] = {"day": {}, "person": {}, "room": {}, "model": {}}
        for e in es:
            for key, val in (("day", e["time"][:10]), ("person", e.get("person") or "unknown"),
                             ("room", e.get("room") or "unknown"), ("model", e.get("model"))):
                b = by[key].setdefault(val, {"calls": 0, "cost": 0.0, "seconds": 0.0})
                b["calls"] += 1
                b["cost"] += e.get("cost", 0.0)
                b["seconds"] += e.get("seconds", 0.0)
        for group in by.values():
            for b in group.values():
                b["avg_seconds"] = round(b["seconds"] / max(b["calls"], 1), 2)
                b["cost"] = round(b["cost"], 4)
                b["seconds"] = round(b["seconds"], 1)
        total = round(sum(e.get("cost", 0.0) for e in es), 4)
        today = time.strftime("%Y-%m-%d")
        return {"days": days, "calls": len(es), "cost": total, "today": by["day"].get(today, {"calls": 0, "cost": 0.0}),
                **by}


def _connection_error(e: Exception) -> bool:
    name = type(e).__name__
    return name in ("APIConnectionError", "APITimeoutError", "ConnectionError", "ConnectTimeout",
                    "ConnectError") or isinstance(e, (ConnectionError, TimeoutError))


class _Messages:
    def __init__(self, outer, inner) -> None:
        self._outer, self._inner = outer, inner

    def create(self, **kw):
        return self._outer._call(lambda c: c.create, kw, getattr(self._inner, "create"))

    def stream(self, **kw):
        return _MeteredStream(self._outer, getattr(self._inner, "stream"), kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _MeteredStream:
    def __init__(self, outer, open_stream, kw) -> None:
        self.outer, self.open_stream, self.kw = outer, open_stream, kw
        self.cm = self.st = None
        self.t0 = time.monotonic()
        self.offline = False

    def __enter__(self):
        try:
            self.cm = self.open_stream(**self.kw)
            self.st = self.cm.__enter__()
        except Exception as e:
            fb = self.outer.fallback
            if fb is None or not _connection_error(e):
                raise
            log.warning("AI unreachable (%s): answering with the offline model", e)
            self.offline = True
            self.cm = fb.beta.messages.stream(**_for_local(self.kw))
            self.st = self.cm.__enter__()
        return self

    @property
    def text_stream(self):
        return self.st.text_stream

    def get_final_message(self):
        msg = self.st.get_final_message()
        self.outer.meter.record("offline" if self.offline else self.kw.get("model", "?"),
                                getattr(msg, "usage", None), time.monotonic() - self.t0, offline=self.offline)
        return msg

    def __exit__(self, *exc):
        return self.cm.__exit__(*exc) if self.cm is not None else False


def _for_local(kw: dict) -> dict:
    """A request the local model can take: only our own tools (no Claude server tools)."""
    out = {k: v for k, v in kw.items() if k not in ("betas", "fallbacks")}
    if out.get("tools"):
        out["tools"] = [t for t in out["tools"] if "input_schema" in t]
    return out


class MeteredClient:
    """Wraps the AI client: counts every call, and falls back to a local model when offline."""

    def __init__(self, inner, home, fallback=None) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "meter", Meter(home))
        object.__setattr__(self, "fallback", fallback)
        object.__setattr__(self, "beta", type("B", (), {})())
        self.beta.messages = _Messages(self, inner.beta.messages)
        if hasattr(inner, "messages"):
            object.__setattr__(self, "messages", _Messages(self, inner.messages))

    def _call(self, pick, kw, fn):
        t0 = time.monotonic()
        try:
            resp = fn(**kw)
            offline = False
        except Exception as e:
            if self.fallback is None or not _connection_error(e):
                raise
            log.warning("AI unreachable (%s): answering with the offline model", e)
            resp = self.fallback.beta.messages.create(**_for_local(kw))
            offline = True
        self.meter.record("offline" if offline else kw.get("model", "?"), getattr(resp, "usage", None),
                          time.monotonic() - t0, kind=getattr(_ctx, "kind", "answer"), offline=offline)
        return resp

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)  # e.g. a local model's smart/fast mode


def metered(client, home):
    if client is None or isinstance(client, MeteredClient):
        return client
    fallback = None
    off = _cfg(home).get("offline")
    if off:
        try:
            from .local_ai import LocalClient

            fallback = LocalClient(off)
        except Exception as e:
            log.error("offline AI not set up: %s", e)
    try:
        return MeteredClient(client, home, fallback)
    except Exception as e:  # a client without the usual shape (tests): leave it
        log.debug("AI meter not used: %s", e)
        return client


# ---------- answer cache ----------
SAFE_TOOLS = {"weather", "web_search", "read_webpage", "calendar", "commute", "diary", "household_facts", "why"}


class AnswerCache:
    def __init__(self, home) -> None:
        self.minutes = float(_cfg(home).get("cache_minutes", 3))
        self.items: dict[tuple, tuple[float, str]] = {}

    @staticmethod
    def key(question: str, inst_id: str, person: str | None) -> tuple:
        from .matcher import normalize

        return (normalize(question), inst_id, person)

    def get(self, question: str, inst_id: str, person: str | None) -> str | None:
        if self.minutes <= 0:
            return None
        hit = self.items.get(self.key(question, inst_id, person))
        if hit and time.monotonic() - hit[0] < self.minutes * 60:
            return hit[1]
        return None

    def put(self, question: str, inst_id: str, person: str | None, answer: str, used: list[str]) -> None:
        from .wordbank import quick_intent

        if self.minutes <= 0 or quick_intent(question) == "task" or set(used) - SAFE_TOOLS:
            return  # it changed something, or might have
        self.items[self.key(question, inst_id, person)] = (time.monotonic(), answer)


def cache_for(home) -> AnswerCache:
    c = home.__dict__.get("_answer_cache")
    if c is None:
        c = home.__dict__["_answer_cache"] = AnswerCache(home)
    return c


# ---------- what goes with every question ----------
_morning: dict[str, tuple[float, str]] = {}


def extra_context(home, inst, person: str | None) -> str:
    """Today's weather, the next calendar events, homework mode and this person's answer style."""
    parts = []
    hit = _morning.get(inst.id)
    if hit and time.monotonic() - hit[0] < 600:
        parts.append(hit[1])
    else:
        bits = []
        w = getattr(home, "weather", None)
        if w is not None:
            try:
                bits.append(f"weather today: {w.summary(0)}")
            except Exception as e:
                log.debug("weather for context: %s", e)
        if getattr(inst, "calendar", None):
            try:
                from .calendar_feed import upcoming

                evs = upcoming(inst.calendar, 1, 0)[:4]
                if evs:
                    bits.append("calendar today: " + "; ".join(
                        f"{e.get('start', '')[11:16]} {e.get('title') or e.get('summary', '')}".strip() for e in evs))
            except Exception as e:
                log.debug("calendar for context: %s", e)
        text = "; ".join(bits)
        _morning[inst.id] = (time.monotonic(), text)
        if text:
            parts.append(text)
    style = Styles(home).get(person or inst.id)
    if style:
        parts.append(f"answer style for this person: {STYLE_TEXT[style]}")
    if homework_mode(home, inst, person):
        parts.append("HOMEWORK MODE: tutor them (see the homework instructions)")
    rec = recipe_now(home, None)
    if rec:
        parts.append(f"cooking: {rec['title']}, step {rec['i'] + 1} of {len(rec['steps'])}")
    return ("[" + "; ".join(parts) + "]") if parts else ""


PROMPT_EXTRAS = """
Being honest: if web results disagree, or you're not sure, say so briefly ("I think...", "reports
differ") instead of sounding certain.

Reading things: to read a letter, a parcel label or a note someone holds up, use look_at_camera on the
door camera and say what it says. Summarize; never read out account, card or ID numbers in full.

Cooking: when someone wants to cook something, get the recipe (web search if needed) and call the recipe
tool with action start, the steps in order (short, one action each) and the ingredients. Then say the
first step. They can say "next step" without your help.

Household facts (bin day, the plumber, passwords): use the household_facts tool. To explain why something
happened ("why did the lights come on?"), use the why tool.
"""

HOMEWORK = """
Homework: in homework mode you are a patient, funny tutor, not an answer machine. Help them get there
themselves: ask what they've tried, give one small step or hint at a time, check they're with you ("make
sense?"), and only show a full worked answer after they've had a real go. For maths, say symbols the way
a person would ("x squared", "the derivative of"). For advanced maths, calculus included (limits,
derivatives, integrals, the chain rule, integration by parts, series, differential equations), break it
into the tiniest steps and explain each with a silly, vivid everyday picture, like: a derivative is a
speedometer for a function; the chain rule is a set of gears, multiply how fast each one turns; an
integral is adding up lots of very thin slices of pizza; a limit is sneaking up on a number without
touching it. Keep jokes short, and make sure the maths is exactly right. Each reply: a few short spoken
sentences, ending with a question, so they answer and you go on.
"""


def system_extras(home, inst) -> str:
    return PROMPT_EXTRAS + (HOMEWORK if (inst.kid_mode or (home.config.extra.get("homework") or {})) else "")


def homework_mode(home, inst, person: str | None) -> bool:
    if home.__dict__.get("_homework_until", {}).get(person or inst.id, 0) > time.monotonic():
        return True
    people = [str(p).lower() for p in (home.config.extra.get("homework") or {}).get("people") or []]
    return bool(person and person.lower() in people) or bool(inst.kid_mode and people == [])


def start_homework(home, who: str, minutes: float = 90) -> None:
    home.__dict__.setdefault("_homework_until", {})[who] = time.monotonic() + minutes * 60


def max_sentences(home, room: str | None, person: str | None) -> int:
    inst = home.instances.for_room(room)
    if homework_mode(home, inst, person):
        return 8
    return {"short": 2, "long": 7}.get(Styles(home).get(person or inst.id) or "", 4)


# ---------- answer styles ----------
STYLE_TEXT = {"short": "very short, one sentence", "long": "fuller, up to six sentences with detail"}
_SHORTER = re.compile(r"^(?:that was |that's |you're |your answers are |way )?(?:too long|too wordy|"
                      r"shorter(?: please)?|keep it short|be brief|less talking|get to the point|tldr)$")
_LONGER = re.compile(r"^(?:more detail(?:s)?(?: please)?|tell me more|explain more|go deeper|too short|"
                     r"give me more detail|longer answers(?: please)?)$")
_NORMAL = re.compile(r"^(?:normal answers|answer normally|back to normal answers)$")


class Styles:
    def __init__(self, home) -> None:
        self.path = data_dir(home) / "answer_styles.json"

    def all(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, who: str | None) -> str | None:
        return self.all().get(who or "") if who else None

    def set(self, who: str, style: str | None) -> None:
        d = self.all()
        if style:
            d[who] = style
        else:
            d.pop(who, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d, indent=1), encoding="utf-8")


def style_request(said: str) -> str | None:
    """'short' / 'long' / 'normal' for "too long" / "more detail" / "answer normally"."""
    if _SHORTER.match(said):
        return "short"
    if _LONGER.match(said):
        return "long"
    if _NORMAL.match(said):
        return "normal"
    return None


# ---------- recipes ----------
_NEXT = re.compile(r"^(?:next|next step|what's next|what next|ok next|okay next|done next|and then|go on)$")
_BACK = re.compile(r"^(?:go back|back|previous step|last step|the step before|one step back)$")
_REPEAT = re.compile(r"^(?:repeat that step|repeat the step|what was that step|what's this step|repeat step|"
                     r"what step am i on|which step)$")
_INGREDIENTS = re.compile(r"^(?:ingredients|what are the ingredients|what do i need|read the ingredients)$")
_STOP_COOKING = re.compile(r"^(?:stop the recipe|i'm done cooking|done cooking|cancel the recipe|end the recipe)$")
RECIPE_WORDS = sorted(set("next step what's what go on back previous last the before one repeat that was "
                          "this am i on which ingredients are do need read stop recipe done cooking cancel end "
                          "ok okay and then".split()))


def recipe_now(home, room: str | None) -> dict | None:
    recs = home.__dict__.get("_recipes") or {}
    r = recs.get(room) or (next(iter(recs.values())) if room is None and recs else None)
    return r if r and time.monotonic() - r["t"] < 3 * 3600 else None


def recipe_step(home, room: str | None, said: str) -> str | None:
    """While a recipe's on: "next step" and friends, answered right away. None if it isn't one."""
    r = recipe_now(home, room)
    if r is None:
        return None
    steps = r["steps"]
    if _NEXT.match(said):
        if r["i"] + 1 >= len(steps):
            return f"That was the last step. Enjoy the {r['title']}."
        r["i"] += 1
    elif _BACK.match(said):
        r["i"] = max(0, r["i"] - 1)
    elif _REPEAT.match(said):
        pass
    elif _INGREDIENTS.match(said):
        return "You need: " + ", ".join(r["ingredients"]) + "." if r["ingredients"] else "I don't have the ingredients."
    elif _STOP_COOKING.match(said):
        home.__dict__["_recipes"].pop(room, None)
        return "Recipe closed."
    else:
        return None
    r["t"] = time.monotonic()
    return f"Step {r['i'] + 1} of {len(steps)}: {steps[r['i']]}"


def recipe_tool(home, args: dict, room: str | None) -> str:
    recs = home.__dict__.setdefault("_recipes", {})
    action = args.get("action", "start")
    if action == "start":
        steps = [s for s in args.get("steps") or [] if s.strip()]
        if not steps:
            raise ValueError("give the steps")
        recs[room] = {"title": args.get("title") or "it", "steps": steps,
                      "ingredients": list(args.get("ingredients") or []), "i": 0, "t": time.monotonic()}
        return f"recipe started: {len(steps)} steps. Say the first one: {steps[0]}"
    said = {"next": "next", "back": "go back", "repeat": "repeat that step", "ingredients": "ingredients",
            "stop": "stop the recipe"}.get(action, action)
    return recipe_step(home, room, said) or "no recipe going"


# ---------- household facts ----------
class Facts:
    def __init__(self, home) -> None:
        self.home = home
        self.path = data_dir(home) / "facts.json"

    def _saved(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def all(self) -> dict[str, dict]:
        out = {}
        for k, v in {**(self.home.config.extra.get("facts") or {}), **self._saved()}.items():
            if isinstance(v, dict) and "value" in v:
                out[str(k).lower()] = {"value": v["value"], "secret": bool(v.get("secret"))}
            else:
                out[str(k).lower()] = {"value": v, "secret": "password" in str(k).lower()}
        return out

    def secrets(self) -> list[str]:
        return [str(v["value"]) for v in self.all().values() if v["secret"] and str(v["value"])]

    def visible(self, inst) -> dict:
        ok_secret = inst.main and not inst.kid_mode
        return {k: (v["value"] if ok_secret or not v["secret"] else "(secret: only told in the main room)")
                for k, v in self.all().items()}

    def set(self, key: str, value: Any, secret: bool = False) -> None:
        d = self._saved()
        d[key.lower()] = {"value": value, "secret": secret or "password" in key.lower()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(d, indent=1), encoding="utf-8")

    def remove(self, key: str) -> bool:
        d = self._saved()
        hit = d.pop(key.lower(), None) is not None
        self.path.write_text(json.dumps(d, indent=1), encoding="utf-8")
        return hit


def birthdays(home, days: int = 14) -> list[tuple[str, int]]:
    """[(name, days until)] within `days`, from facts: {birthdays: {Sam: "03-14"}}."""
    raw = Facts(home).all().get("birthdays", {}).get("value") or {}
    today = clock.now().date()
    out = []
    for name, md in (raw.items() if isinstance(raw, dict) else []):
        try:
            m, d = (int(x) for x in str(md).split("-")[-2:])
            when = today.replace(month=m, day=d)
            if when < today:
                when = when.replace(year=today.year + 1)
            if (when - today).days <= days:
                out.append((str(name), (when - today).days))
        except (ValueError, TypeError):
            continue
    return sorted(out, key=lambda x: x[1])


# ---------- tools for the AI ----------
S = {"type": "string"}


def _tool(name, description, props, required=None):
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required or list(props),
                             "additionalProperties": False}}


def tools_for(home, inst) -> list[dict]:
    out = [
        _tool("household_facts", "The household's facts: bin day, who to call, birthdays, passwords. list, get, set or "
              "remove. Secret ones only show in the main room.", {
                  "action": {"type": "string", "enum": ["list", "get", "set", "remove"]},
                  "key": S, "value": S, "secret": {"type": "boolean"}}),
        _tool("why", "Why something happened in the house (lights, locks, modes, alarms): the recent commands, "
              "automations, schedules and who or what triggered them.", {
                  "about": {"type": "string", "description": "What happened, e.g. 'kitchen lights on', or empty."}}),
        _tool("recipe", "Cook along: start a recipe (title, steps in order, ingredients), or next/back/repeat/"
              "ingredients/stop.", {
                  "action": {"type": "string", "enum": ["start", "next", "back", "repeat", "ingredients", "stop"]},
                  "title": S, "steps": {"type": "array", "items": S}, "ingredients": {"type": "array", "items": S}}),
    ]
    if not inst.kid_mode or (home.config.extra.get("homework") or {}):
        out.append(_tool("homework_mode", "Start tutoring mode for the person speaking (homework help), for a while.",
                         {"minutes": {"type": "number", "description": "How long, e.g. 60."}}))
    return out


def run_tool(home, name: str, args: dict, room: str | None, inst, person: str | None = None) -> Any:
    if name == "household_facts":
        f = Facts(home)
        key = (args.get("key") or "").lower()
        if args["action"] == "list":
            return json.dumps(f.visible(inst))
        if args["action"] == "get":
            vis = f.visible(inst)
            hit = vis.get(key) or next((v for k, v in vis.items() if key and key in k), None)
            return json.dumps({key: hit}) if hit is not None else f"no fact called {key}"
        if inst.kid_mode:
            raise PermissionError("kids' rooms can't change household facts")
        if args["action"] == "set":
            f.set(key, args.get("value", ""), bool(args.get("secret")))
            return "saved"
        return "removed" if f.remove(key) else f"no fact called {key}"
    if name == "why":
        kinds = ("command", "automation", "schedule", "routine", "door", "mode", "presence", "scan", "alarm",
                 "assistant", "wake", "security", "vacation", "follow", "energy")
        evs = [e for e in home.events.query(limit=300) if str(e.get("kind", "")).split(".")[0] in kinds][-40:]
        return json.dumps(evs, default=str)
    if name == "recipe":
        return recipe_tool(home, args, room)
    if name == "homework_mode":
        start_homework(home, person or inst.id, float(args.get("minutes") or 60))
        return "homework mode on"
    raise KeyError(name)


TOOL_NAMES = {"household_facts", "why", "recipe", "homework_mode"}


# ---------- proactive: at most so often ----------
def proactive_ok(home) -> bool:
    per_hour = float((home.config.extra.get("watcher") or {}).get("max_per_hour", 1))
    said = home.__dict__.setdefault("_proactive", [])
    now = time.monotonic()
    said[:] = [t for t in said if now - t < 3600]
    if len(said) >= per_hour:
        return False
    said.append(now)
    return True


# ---------- weekly summary ----------
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def weekly_due(home, now=None) -> bool:
    cfg = _cfg(home).get("weekly", {"day": "sun", "time": "18:00"})
    if cfg is False:
        return False
    now = now or clock.now()
    day, at = str(cfg.get("day", "sun"))[:3].lower(), clock.parse_hhmm(str(cfg.get("time", "18:00")))
    if DAYS[now.weekday()] != day or (now.hour, now.minute) < (at.hour, at.minute):
        return False
    week = now.strftime("%G-%V")
    if home.__dict__.get("_weekly_done") == week:
        return False
    home.__dict__["_weekly_done"] = week
    return True


def weekly_facts(home) -> dict:
    since = (clock.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    evs = home.events.query(since=since, limit=5000) if hasattr(home.events, "query") else []
    security = [e for e in evs if str(e.get("kind", "")).split(".")[0] in ("scan", "alarm", "security", "smoke",
                                                                             "safety", "doorbell")]
    energy = None
    if getattr(home, "energy", None):
        try:
            energy = home.energy.report("week")
        except Exception as e:
            log.debug("weekly energy: %s", e)
    meter = Meter(home).summary(7)
    return {"security_events": len(security), "security": [{"kind": e.get("kind"), "time": e.get("time"),
                                                            "who": e.get("name") or e.get("person")}
                                                           for e in security[-15:]],
            "energy": energy, "birthdays_soon": birthdays(home, 14), "ai_cost_week": meter["cost"]}


def weekly_summary(home) -> str:
    from . import ai

    facts = weekly_facts(home)
    try:
        text = ai.ask(home, "Write the house's weekly summary to be read aloud on Sunday evening: three or four "
                            "short spoken sentences. Who used the most energy, anything security-related worth "
                            "knowing, and birthdays coming up. Plain speech, no lists.\n" + json.dumps(facts, default=str),
                      quick=True, max_tokens=1024)
    except Exception as e:
        log.info("weekly summary without the AI (%s)", e)
        bits = [f"{facts['security_events']} security events this week"]
        bits += [f"{n}'s birthday is in {d} days" if d else f"it's {n}'s birthday today"
                 for n, d in facts["birthdays_soon"]]
        text = "Weekly summary: " + "; ".join(bits) + "."
    return text
