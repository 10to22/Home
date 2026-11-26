"""Understanding more of what people actually say:

Numbers       "seventy two and a half", "72.5" and "seventy-two" all match "{temp}" slots. In the config,
              {numbers: [60, 85], halves: true} adds "... and a half" values.
Relative      "brighter", "a bit warmer", "louder", "turn it down a lot": changes the level from where it is
              (the last value set by voice, or a sensor). Set up in `relative:` (below).
Pronouns      after "turn on the kitchen lights", "turn them off" means the kitchen lights.
Groups        "turn off the upstairs lights": every room in the group. `groups:` (below); "everywhere"
              and "the whole house" are every room by default.
Timed chains  "lock up, then turn off the lights in five minutes": the second part runs later.
Teach by voice  "house, when I say cozy mode, dim the lights and play jazz": made straight away
              (no AI needed) when every part is a command you already have.

    groups:
      upstairs: [bedroom, office]
      downstairs: [kitchen, living room]
    relative:
      brightness: { command: dim, slot: level, step: 20, min: 10, max: 100,
                    up: [brighter, lighter], down: [dimmer, darker] }
      temperature: { command: set_temp, slot: temp, step: 1, min: 60, max: 80, start: 70,
                     up: [warmer, hotter], down: [cooler, colder], sensor: [hallway, temperature] }
      volume: { command: set_volume, slot: level, step: 10, min: 0, max: 100,
                up: [louder, turn it up], down: [quieter, softer, turn it down] }
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger("phrasehome.understanding")


# ---------- numbers ----------
_SMALL = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "a": 1, "an": 1}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90}
_FRACTIONS = {"and a half": 0.5, "and a quarter": 0.25, "and three quarters": 0.75, "point five": 0.5}


def words_to_number(text: str) -> float | None:
    """'seventy two and a half' -> 72.5, 'one hundred twenty' -> 120, '72.5' -> 72.5. None if it isn't one."""
    t = text.strip().lower().replace("-", " ")
    try:
        return float(t)
    except ValueError:
        pass
    frac = 0.0
    for words, value in _FRACTIONS.items():
        if t.endswith(" " + words) or t == words.replace("and ", ""):
            frac, t = value, t[: -len(words)].strip()
            break
    if t in ("half", "a half"):
        return 0.5
    total, cur, seen = 0, 0, False
    for w in t.split():
        if w in _SMALL:
            cur += _SMALL[w]
        elif w in _TENS:
            cur += _TENS[w]
        elif w == "hundred":
            cur = max(cur, 1) * 100
        elif w == "thousand":
            total += max(cur, 1) * 1000
            cur = 0
        elif w == "and":
            continue
        else:
            return None
        seen = True
    return (total + cur + frac) if seen else (frac or None)


def say_number(x: float) -> str:
    from .clock import number_words

    whole, frac = int(x), round(x - int(x), 2)
    words = number_words(whole) if whole or not frac else ""
    tail = {0.5: "and a half", 0.25: "and a quarter", 0.75: "and three quarters"}.get(frac, "")
    return f"{words} {tail}".strip() if tail else number_words(whole)


_DIGITS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.])")


def spell_numbers(text: str) -> str:
    """'set the heat to 72.5' -> 'set the heat to seventy two and a half' (Whisper writes digits)."""
    def one(m: re.Match) -> str:
        x = float(m.group(1))
        return say_number(x) if x < 100000 and (x == int(x) or round(x % 1, 2) in (0.25, 0.5, 0.75)) else m.group(1)

    return _DIGITS.sub(one, text.replace("°", " ").replace("%", " percent"))


def add_halves(slot: dict[str, str]) -> dict[str, str]:
    """{numbers: [60, 85], halves: true}: also "sixty and a half" -> 60.5 ..."""
    out = dict(slot)
    for spoken, value in slot.items():
        try:
            v = float(value)
        except ValueError:
            continue
        if v == int(v) and spoken not in ("a",):
            out[f"{spoken} and a half"] = f"{v + 0.5:g}"
    return out


# ---------- the last command, per room ----------
def last_command(home, room: str | None, seconds: float = 600):
    last = (home.__dict__.get("_last_command") or {}).get(room)
    return last[1] if last and time.monotonic() - last[0] < seconds else None


def remember_level(home, cmd_name: str, room: str | None, slot: str, value: Any) -> None:
    home.__dict__.setdefault("_levels", {})[(cmd_name, room, slot)] = value


# ---------- pronouns ----------
PRONOUNS = ("them", "it", "those", "that", "these", "this")
_PARTICLES = {"on", "off", "up", "down", "to", "in", "at", "for", "back", "out", "please"}


def pronoun_forms(text: str, spoken: list[str]) -> list[str]:
    """'turn off the kitchen lights' with spoken slot words ['kitchen'] -> 'turn off them', 'turn them off', ..."""
    words = text.split()
    for sp in spoken:
        sw = sp.split()
        for i in range(len(words) - len(sw) + 1):
            if words[i:i + len(sw)] != sw:
                continue
            start = i - 1 if i > 0 and words[i - 1] in ("the", "my", "our") else i
            end = i + len(sw)
            if end < len(words) and words[end] not in _PARTICLES:
                end += 1  # the noun: "lights"
            before, after = words[:start], words[end:]
            out = []
            for p in PRONOUNS:
                out.append(" ".join(before + [p] + after))
                if before and after and after[0] in _PARTICLES:  # "turn off them" -> "turn them off"
                    out.append(" ".join(before[:-1] + [p] + [before[-1]] + after) if before[-1] in _PARTICLES
                               else " ".join(before + [p] + after))
                if before and before[-1] in _PARTICLES:
                    out.append(" ".join(before[:-1] + [p, before[-1]] + after))
            return out
    return []


def resolve_pronoun(text: str, matcher, home, config, room: str | None) -> list:
    """'turn them off' right after a command about the kitchen lights -> that command for the kitchen."""
    from rapidfuzz import fuzz

    from .matcher import Match, normalize

    said = normalize(text)
    if not any(f" {p} " in f" {said} " for p in PRONOUNS):
        return []
    prev = last_command(home, room, 300)
    if prev is None or not prev.slots:
        return []
    spoken = []
    for name, value in prev.slots.items():
        spoken += [normalize(k) for k, v in (config.slots.get(name) or {}).items() if v == value]
    best = None
    for cand in matcher.candidates:
        if cand.slots != prev.slots:
            continue
        for form in pronoun_forms(cand.text, spoken):
            score = fuzz.ratio(said, form)
            if score >= matcher.threshold and (best is None or score > best[0]):
                best = (score, cand)
    if best is None:
        return []
    score, cand = best
    return [Match(cand.command, cand.text, dict(cand.slots), score, said, via="pronoun")]


# ---------- groups of rooms ----------
DEFAULT_ALL = ("everywhere", "the whole house", "the house", "all rooms", "every room", "all the rooms")


def groups(config) -> dict[str, list[str]]:
    g = {str(k).lower(): [str(r).lower() for r in v] for k, v in (config.extra.get("groups") or {}).items()}
    rooms = sorted({str(v).lower() for v in (config.slots.get("room") or {}).values()})
    for name in DEFAULT_ALL:
        g.setdefault(name, rooms)
    return g


def resolve_group(text: str, matcher, config) -> list:
    """'turn off the upstairs lights' -> the room command once per room upstairs."""
    from .matcher import normalize

    said = normalize(text)
    room_slot = config.slots.get("room") or {}
    if not room_slot:
        return []
    for gname, members in sorted(groups(config).items(), key=lambda kv: -len(kv[0])):
        if not members or not re.search(rf"\b{re.escape(gname)}\b", said):
            continue
        spoken = next((k for k, v in room_slot.items() if v.lower() == members[0]), None)
        if spoken is None:
            continue
        trial = re.sub(rf"\b(?:in |on )?{re.escape(gname)}\b", spoken, said, count=1)
        m = matcher.match(trial) or matcher.match(re.sub(rf"\b{re.escape(gname)}\b", spoken, said, count=1))
        if m is None or m.slots.get("room", "").lower() != members[0]:
            continue
        out = []
        for r in members:
            value = next((v for v in room_slot.values() if v.lower() == r), r)
            out.append(type(m)(m.command, m.phrase, {**m.slots, "room": value}, m.score, said, via="group"))
        log.info("group %s: %s in %d rooms", gname, m.command.name, len(out))
        return out
    return []


# ---------- relative changes ----------
_A_BIT = re.compile(r"\b(?:a (?:little )?bit|a little|slightly|a touch|just a bit)\b")
_A_LOT = re.compile(r"\b(?:a lot|much|way|loads|heaps)\b")


def resolve_relative(text: str, home, config, room: str | None) -> list:
    """'a bit brighter' -> the brightness command with the level one (half) step up."""
    from .matcher import Match, normalize

    said = normalize(text)
    for target, r in (config.extra.get("relative") or {}).items():
        up = [normalize(w) for w in r.get("up") or []]
        down = [normalize(w) for w in r.get("down") or []]
        direction = 0
        if any(re.search(rf"\b{re.escape(w)}\b", said) for w in up):
            direction = 1
        elif any(re.search(rf"\b{re.escape(w)}\b", said) for w in down):
            direction = -1
        if not direction:
            continue
        cmd = next((c for c in config.commands if c.name == r.get("command")), None)
        slot = r.get("slot")
        options = config.slots.get(slot or "") or {}
        if cmd is None or not options:
            log.warning("relative %s: command %r / slot %r not found", target, r.get("command"), slot)
            return []
        step = float(r.get("step", 10))
        if _A_BIT.search(said):
            step /= 2
        elif _A_LOT.search(said):
            step *= 2
        cur = current_level(home, r, cmd.name, room, slot)
        want = min(float(r.get("max", 1e9)), max(float(r.get("min", -1e9)), cur + direction * step))
        values = {}
        for spoken, v in options.items():
            try:
                values[v] = float(v)
            except ValueError:
                pass
        if not values:
            return []
        value = min(values, key=lambda v: abs(values[v] - want))
        if values[value] == cur and len(values) > 1:  # nearest is where we are: go one further
            ahead = [v for v in values if (values[v] - cur) * direction > 0]
            if ahead:
                value = min(ahead, key=lambda v: abs(values[v] - cur))
        slots = {slot: value}
        if "{here}" in str(cmd.action) and room:
            slots["here"] = room
        log.info("relative: %s %s -> %s (%s)", target, cur, value, said)
        return [Match(cmd, cmd.phrases[0], slots, 90.0, said, via="relative")]
    return []


def current_level(home, r: dict, cmd_name: str, room: str | None, slot: str) -> float:
    if r.get("sensor") and getattr(home, "sensors", None):
        node, inp = r["sensor"]
        cur = home.sensors.current(node, inp) or {}
        try:
            return float(cur.get("value"))
        except (TypeError, ValueError):
            pass
    levels = home.__dict__.get("_levels") or {}
    for key in ((cmd_name, room, slot), (cmd_name, None, slot)):
        if key in levels:
            try:
                return float(levels[key])
            except (TypeError, ValueError):
                pass
    return float(r.get("start", (float(r.get("min", 0)) + float(r.get("max", 100))) / 2))


# ---------- timed chains ----------
_LATER = re.compile(r"\s+(?:in|after)\s+(?P<n>[a-z0-9.\- ]+?)\s+(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)$")
_LATER_WORDS = {"in a minute": 60, "in a sec": 5, "in a second": 5, "in half an hour": 1800, "in an hour": 3600,
                "in a few minutes": 180, "in a couple of minutes": 120, "in a couple minutes": 120}


def split_later(part: str) -> tuple[str, float]:
    """'turn off the lights in five minutes' -> ('turn off the lights', 300)."""
    for words, secs in _LATER_WORDS.items():
        if part.endswith(" " + words):
            return part[: -len(words)].strip(), float(secs)
    m = _LATER.search(part)
    if not m:
        return part, 0.0
    n = words_to_number(m.group("n"))
    if n is None:
        return part, 0.0
    unit = m.group("unit")
    mult = 3600 if unit.startswith("h") else 1 if unit.startswith("s") else 60
    return part[: m.start()].strip(), float(n) * mult


# ---------- teach by voice ----------
_TEACH = re.compile(r"^(?:when|whenever|if) (?:i|we) say (?P<phrase>.+?)(?: then| you should| please| can you| could you)?"
                    r" (?P<what>(?:turn|switch|dim|set|lock|play|start|stop|open|close|run|put|make|shut|kill|"
                    r"raise|lower|mute|unmute|pause|resume)\b.+)$")


def teach(text: str, matcher, home, config) -> str | None:
    """'when I say cozy mode dim the lights and play jazz' -> a new command, if every part is a command.
    Returns what to say, or None if it isn't one (the AI's customize tool does the harder ones)."""
    from .listener import commands_in
    from .matcher import normalize

    m = _TEACH.match(normalize(text))
    customs = getattr(home, "customs", None)
    if not m or customs is None:
        return None
    phrase = m.group("phrase").strip()
    parts = commands_in(m.group("what"), matcher, config)
    if not parts:
        return None
    items = [{"command": p.command.name, "slots": dict(p.slots)} for p in parts]
    try:
        customs.add_command(phrase, [phrase], items)
    except ValueError as e:
        return f"I can't: {e}."
    engine = getattr(home, "engine", None)
    if engine is not None:
        engine.reload()
    names = ", then ".join(p.phrase for p in parts)
    return f"Done. When you say {phrase}, I'll {names}."


# ---------- all of it, for the listener ----------
def understand(text: str, matcher, home, config, room: str | None):
    """Before normal matching: (matches, delay seconds per match, something to say) or None."""
    from .listener import commands_in
    from .matcher import normalize

    said = normalize(spell_numbers(text))
    taught = teach(said, matcher, home, config)
    if taught:
        return [], [], taught
    for fn in (lambda t: resolve_relative(t, home, config, room), lambda t: resolve_group(t, matcher, config),
               lambda t: resolve_pronoun(t, matcher, home, config, room)):
        got = fn(said)
        if got:
            return got, [0.0] * len(got), None
    # timed chains: every part a command (or a pronoun for the one before), some of them later
    from .listener import _JOINERS

    parts = [p for p in _JOINERS.split(said) if p.strip()]
    if not any(split_later(p)[1] for p in parts):
        if said != normalize(text) and matcher.match(said):  # digits spelled out
            m = matcher.match(said)
            return [m], [0.0], None
        return None
    out, delays = [], []
    for p in parts:
        core, delay = split_later(p)
        got = commands_in(core, matcher, config) or resolve_pronoun(core, matcher, home, config, room)
        if not got and out:  # "...then turn them off": about the part before
            prev = out[-1]
            fake = home.__dict__.setdefault("_last_command", {})
            saved = fake.get(room)
            fake[room] = (time.monotonic(), prev)
            got = resolve_pronoun(core, matcher, home, config, room)
            if saved is None:
                fake.pop(room, None)
            else:
                fake[room] = saved
        if not got:
            return None
        out += got
        delays += [delay] * len(got)
    return out, delays, None
