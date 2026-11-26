"""The house's word bank: more ways of saying the same thing, and a quick sense of what a sentence is.

variants()      every command phrase also covers the usual other ways of saying it:
                "turn on the {room} lights" -> "switch on the {room} lights", "turn the {room} lights on",
                "{room} lights on", "flick the {room} lights on", "whack the {room} lights on"...
                (settings: phrase_variants, on by default). The wordings are in vocabulary.py.
place_names()   other names for rooms: "lounge" / "front room" = living room, "loo" = bathroom
strip_filler()  the polite bits around a command don't stop it matching:
                "could you please turn on the lights for me" -> "turn on the lights"
quick_intent()  "task" or "question" when the first words make it obvious, None when only the AI can tell
                ("house party was fun" is neither, and shouldn't get an answer)

Everything here is English. Other languages keep their phrases exactly as written in the config.
"""

from __future__ import annotations

import re

# ---------- other ways of saying a command ----------
# The wordings themselves live in vocabulary.py (FAMILIES, PHRASES, THINGS, PLACES).
from . import vocabulary as _vocab  # noqa: E402

_SET_TO = re.compile(r"^set (.+) to (.+)$")
# kept for anyone who added their own the old way: word -> other words
SYNONYMS: dict[str, list[str]] = _vocab.THINGS

MAX_VARIANTS = 6  # per phrase by default (the Pi); a laptop without a fixed vocabulary uses far more
BIG_VARIANTS = 40

_DETERMINERS = {"some", "a", "an", "my", "our", "your", "his", "her", "their", "this", "that", "these",
                "those", "all", "any", "the", "every"}
# "{x} on" / "{x} up" fit a lot of everyday verbs ("hold on", "wake up", "calm down"): those aren't things
_PHRASAL = {
    "hold", "come", "game", "log", "sign", "back", "calm", "shut", "lie", "sit", "wake", "get", "give", "hang",
    "switch", "turn", "put", "carry", "move", "keep", "go", "cheer", "hurry", "speak", "listen", "set", "lock",
    "dress", "break", "slow", "settle", "wind", "cool", "warm", "pipe", "show", "grow", "look", "pick", "catch",
    "clean", "tidy", "stand", "step", "drop", "fill", "sum", "work", "freak", "chill", "power", "fire", "start",
    "boot", "bring", "cut", "kill", "knock", "call", "check", "write", "read", "shape", "stay", "lay", "run",
    "head", "push", "pull", "play", "mess", "screw", "sober", "wise", "suit", "gear", "rev", "pump", "crank",
    "bump", "ease", "tone", "sell", "make", "take", "shake", "wrap", "close", "open", "end", "shove", "wash",
    "dry", "tune", "zone", "ring", "doze", "nod", "pass", "black", "jack", "shoot", "hit",
    "kick", "perk", "psych", "amp", "lighten", "brighten", "dim", "count", "add", "mix", "pay", "back", "fuck",
    "piss", "bugger", "sleep", "what", "how", "i", "we", "you", "it", "that", "this", "game", "cheers",
}


# people drop "the" after these ("turn on lights", "dim kitchen lights"); after others it changes the
# meaning ("kill time", "hold on"), so it has to be there
_THE_OPTIONAL = {"turn", "switch", "dim", "brighten"}


def _compile(form: str) -> re.Pattern:
    """"turn on the {x}" -> ^turn on (?:the )?(.+)$"""
    toks, out = form.split(), ""
    loose = toks[0] in _THE_OPTIONAL
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t == "{x}":
            out += "(.+)"
        elif t == "the" and nxt == "{x}" and loose:
            out += "(?:the )?"
            continue
        else:
            out += re.escape(t)
        if nxt is not None:
            out += " "
    return re.compile(f"^{out}$")


# per family: (forms to say, matchable forms most specific first). A form starting with "~" is only said,
# never matched ("more {x}" shouldn't make "more coffee" a volume command)
_FAMILIES: list[tuple[list[str], list[tuple[re.Pattern, str]]]] = []
for _forms in _vocab.FAMILIES.values():
    _say = [f.lstrip("~") for f in _forms]
    _match = sorted((f for f in _forms if not f.startswith("~")),
                    key=lambda f: -len(f.replace("{x}", "").replace("the ", "").strip()))
    _FAMILIES.append((_say, [(_compile(f), f) for f in _match]))
_PHRASE_GROUPS = [[k] + v for k, v in _vocab.PHRASES.items()]


def _x_ok(x: str, form: str) -> bool:
    words = x.split()
    if not words:
        return False
    if "the {x}" in form and words[0] in _DETERMINERS:
        return False  # "turn on some music" isn't "turn on the some music"
    if form.startswith("{x}") and (words[0] in _PHRASAL or len(words) > 3):
        return False  # "hold on", "wake up", "calm down"
    return True


def _sounds_right(v: str) -> bool:
    w = v.split()
    return not any(a == b or (a in _DETERMINERS and b in _DETERMINERS) for a, b in zip(w, w[1:]))


def _things(p: str) -> list[str]:
    out = []
    for word, others in _vocab.THINGS.items():
        if re.search(rf"\b{re.escape(word)}\b", p):
            out += [re.sub(rf"\b{re.escape(word)}\b", o, p) for o in others]
    return out


def variants(phrase: str, limit: int | None = MAX_VARIANTS) -> list[str]:
    """Other common ways of saying `phrase` (already lowercase), not including it, best first.
    Slots ({room}) are kept as they are. limit: how many at most (None: all of them)."""
    p = re.sub(r"\s+", " ", phrase.strip()).lower()
    head: list[str] = []  # the most common few of each family
    rest: list[str] = []
    for say, match in _FAMILIES:
        for rx, form in match:
            m = rx.match(p)
            if m and _x_ok(m.group(1).strip(), form):
                x = m.group(1).strip()
                got = [f.replace("{x}", x) for f in say]
                got = [g for g in got if g != p]
                head += got[:4]
                rest += got[4:]
                break
    m = _SET_TO.match(p)
    if m:
        head += [f"change {m.group(1)} to {m.group(2)}", f"put {m.group(1)} to {m.group(2)}",
                 f"make {m.group(1)} {m.group(2)}"]
    phrases = [o for g in _PHRASE_GROUPS if p in g for o in g]
    things = _things(p)
    later_things = [t for h in head[:3] for t in _things(h)]
    out: list[str] = []
    made = set(phrases)  # whole phrases are written by hand ("night night"); the rest is put together
    for v in head + phrases + things + rest + later_things:
        v = re.sub(r"\s+", " ", v).strip()
        if v and v != p and v not in out and (v in made or _sounds_right(v)):
            out.append(v)
    return out if limit is None else out[:limit]


def place_names(slots: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """The slots with the other names for each room added ("lounge" for living room, "loo" for
    bathroom), each meaning the same value. A name a slot already uses is never taken over."""
    groups = [[k] + v for k, v in _vocab.PLACES.items()]
    out = {}
    for name, values in slots.items():
        more = dict(values)
        for spoken, value in values.items():
            low = spoken.lower()
            for g in groups:
                if low in g:
                    for other in g:
                        more.setdefault(other, value)
        out[name] = more
    return out


# ---------- polite filler around a command ----------
LEAD_FILLER = sorted(set([
    "please", "can you", "could you", "would you", "will you", "can you please", "could you please",
    "would you please", "will you please", "would you mind", "do you mind", "go ahead and",
    "i want you to", "i need you to", "i'd like you to", "i would like you to", "i'd like to",
    "let's", "just", "quickly", "um", "uh", "er", "so", "and", "and then", "then", "now", "okay",
    "ok", "alright", "hey", "yo", "oh", "actually", "also", "maybe", "kindly", "be a dear and",
    "do me a favor and", "do me a favour and", "mind",
] + _vocab.LEAD_FILLER), key=lambda s: -len(s))
TRAIL_FILLER = sorted(set([
    "please", "for me", "for me please", "now", "right now", "now please", "thanks", "thank you",
    "thanks a lot", "thank you very much", "if you can", "if you could", "would you", "will you",
    "could you", "real quick", "quickly", "asap", "already", "again", "too", "as well", "for us",
] + _vocab.TRAIL_FILLER), key=lambda s: -len(s))
FILLER_WORDS = sorted({w for p in LEAD_FILLER + TRAIL_FILLER for w in p.split()})


def strip_filler(text: str) -> str:
    """Drop polite words at the start and end: "can you please lock the doors for me" -> "lock the doors".
    `text` is normalized (lowercase words). Never strips a sentence down to nothing."""
    s = f" {text.strip()} "
    changed = True
    while changed:
        changed = False
        for f in LEAD_FILLER:
            if s.startswith(f" {f} ") and len(s.strip()) > len(f):
                s = s[len(f) + 1:]
                changed = True
                break
        for f in TRAIL_FILLER:
            if s.endswith(f" {f} ") and len(s.strip()) > len(f):
                s = s[: -(len(f) + 1)]
                changed = True
                break
    return s.strip()


# ---------- is it a task, a question, or neither? ----------
WH_WORDS = {"what", "what's", "whats", "who", "who's", "whos", "whom", "whose", "when", "when's",
            "where", "where's", "wheres", "why", "why's", "which"}
HOW_NEXT = {"about", "many", "much", "long", "far", "old", "big", "do", "does", "did", "is", "are", "was",
            "were", "can", "could", "should", "would", "will", "to", "come", "often", "soon", "late",
            "early", "cold", "hot", "warm"}
# yes/no questions start with these, but so do remarks with the house's name as the subject
# ("house is a mess"), so they're left to the AI unless there's a question mark
AUX_WORDS = {"is", "are", "was", "were", "am", "do", "does", "did", "can", "could", "will", "would",
             "should", "shall", "has", "have", "had", "may", "might", "isn't", "aren't", "doesn't",
             "didn't", "won't", "wouldn't", "can't", "couldn't", "shouldn't", "hasn't", "haven't"}
ASK_STARTS = ("tell me", "do you know", "any idea", "give me", "i wonder", "remind me what",
              "what about", "how about", "let me know", "guess what") + tuple(_vocab.ASK_STARTS)
POLITE_REQUESTS = ("can you", "could you", "would you", "will you", "please", "i want you to",
                   "i need you to", "i'd like you to", "go ahead and", "would you mind", "do me a favor",
                   "do me a favour", "be a dear", "any chance you could", "any chance you can",
                   "i was wondering if you could", "do us a favour", "do us a favor", "be a legend",
                   "can ya", "could ya")
NEED_STARTS = ("i need", "we need", "i want", "we want", "i'd like", "we'd like", "i would like",
               "we're out of", "we are out of", "i'm out of", "we ran out of", "we're running low",
               "we're low on", "make sure", "don't forget", "do not forget", "let's", "time to", "time for")
# imperative verbs that clearly start a request (not words that are usually nouns after "house")
TASK_VERBS = {
    "turn", "switch", "lock", "unlock", "open", "close", "shut", "dim", "brighten", "set", "reset", "start",
    "stop", "pause", "resume", "unpause", "play", "skip", "replay", "mute", "unmute", "add", "remove",
    "delete", "cancel", "clear", "remind", "wake", "tell", "announce", "show", "read", "find", "check",
    "look", "search", "google", "order", "book", "schedule", "make", "put", "raise", "lower", "increase",
    "decrease", "arm", "disarm", "remember", "forget", "note", "save", "send", "message", "text", "call",
    "ring", "phone", "give", "help", "flip", "roll", "pick", "choose", "sing", "say", "repeat", "explain",
    "describe", "translate", "define", "spell", "calculate", "convert", "recommend", "suggest", "plan",
    "list", "count", "warm", "cool", "heat", "feed", "water", "record", "take", "begin", "finish", "keep",
    "get", "bring", "move", "change", "update", "enable", "disable", "activate", "deactivate", "boost",
    "silence", "quiet", "hush", "broadcast", "notify", "alert", "ask", "answer", "summarize", "summarise",
    "brief", "report", "track", "monitor", "watch", "listen", "queue", "shuffle", "louder", "quieter",
    "volume", "rewind", "forward", "next", "previous", "lights", "light", "goodnight", "goodbye", "learn",
} | set(_vocab.TASK_VERBS)


# verbs that also go with "home" in everyday talk ("take me home", "call home"): not enough on their own
# when the name comes at the end
AMBIGUOUS_AT_END = {"take", "call", "bring", "get", "ring", "phone", "keep", "move", "make", "put", "help",
                    "give", "watch", "listen", "say", "tell"}


def quick_intent(text: str, strict: bool = False) -> str | None:
    """'task' or 'question' when the words make it obvious, else None (let the AI decide).
    `text` is what was said after the house's name. strict: for the name at the end of a sentence,
    only the clearest requests and questions count."""
    raw = text.strip()
    if raw.endswith("?"):
        return "question"
    from .matcher import normalize

    said = normalize(raw)
    if not said or all(w == "unk" for w in said.split()):
        return None
    padded = f"{said} "
    if any(padded.startswith(p + " ") for p in POLITE_REQUESTS):
        return "task"
    if any(padded.startswith(p + " ") for p in ASK_STARTS):  # before needs: "i want to know..."
        return "question"
    if not strict and any(padded.startswith(p + " ") for p in NEED_STARTS):
        return "task"
    words = strip_filler(said).split()
    first, second = words[0], (words[1] if len(words) > 1 else "")
    if first in WH_WORDS:
        if first == "what" and second in ("a", "an"):  # "what a day"
            return None
        return "question"
    if first in ("how", "how's", "hows"):
        return "question" if (first != "how" or second in HOW_NEXT or not second) else None
    if first in TASK_VERBS and len(words) > 1 and not (strict and first in AMBIGUOUS_AT_END):
        return "task"
    return None
