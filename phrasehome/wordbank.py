"""The house's word bank: more ways of saying the same thing, and a quick sense of what a sentence is.

variants()      every command phrase also covers the usual other ways of saying it:
                "turn on the {room} lights" -> "switch on the {room} lights", "turn the {room} lights on",
                "{room} lights on", "put the {room} lights on"... (settings: phrase_variants, on by default)
strip_filler()  the polite bits around a command don't stop it matching:
                "could you please turn on the lights for me" -> "turn on the lights"
quick_intent()  "task" or "question" when the first words make it obvious, None when only the AI can tell
                ("house party was fun" is neither, and shouldn't get an answer)

Everything here is English. Other languages keep their phrases exactly as written in the config.
"""

from __future__ import annotations

import re

# ---------- other ways of saying a command ----------
# (pattern, replacements) on a whole normalized phrase. {x} in a replacement is the captured part.
# Slots like {room} survive because they're only letters and braces.
REWRITES: list[tuple[str, list[str]]] = [
    (r"^turn on (?:the )?(.+)$", ["switch on the {x}", "turn the {x} on", "put the {x} on", "{x} on"]),
    (r"^turn off (?:the )?(.+)$", ["switch off the {x}", "turn the {x} off", "shut off the {x}",
                                   "kill the {x}", "{x} off"]),
    (r"^turn (?:the )?(.+) on$", ["turn on the {x}", "switch the {x} on", "switch on the {x}"]),
    (r"^turn (?:the )?(.+) off$", ["turn off the {x}", "switch the {x} off", "switch off the {x}"]),
    (r"^switch on (?:the )?(.+)$", ["turn on the {x}", "turn the {x} on"]),
    (r"^switch off (?:the )?(.+)$", ["turn off the {x}", "turn the {x} off"]),
    (r"^lock (?:up )?the (.+)$", ["secure the {x}", "lock up the {x}"]),
    (r"^close the (.+)$", ["shut the {x}"]),
    (r"^shut the (.+)$", ["close the {x}"]),
    (r"^open the (.+)$", ["open up the {x}"]),
    (r"^start the (.+)$", ["begin the {x}", "kick off the {x}"]),
    (r"^stop the (.+)$", ["end the {x}", "turn off the {x}"]),
    (r"^play (.+)$", ["put on {x}", "start playing {x}"]),
    (r"^dim (?:the )?(.+)$", ["lower the {x}", "turn down the {x}"]),
    (r"^brighten (?:the )?(.+)$", ["turn up the {x}", "raise the {x}"]),
    (r"^turn up (?:the )?(.+)$", ["raise the {x}", "increase the {x}", "turn the {x} up"]),
    (r"^turn down (?:the )?(.+)$", ["lower the {x}", "decrease the {x}", "turn the {x} down"]),
    (r"^what's (.+)$", ["what is {x}"]),
    (r"^what is (.+)$", ["what's {x}"]),
    (r"^it's (.+)$", ["it is {x}"]),
]
# short "<thing> on/off" phrases (lights on, fan off) also as "turn on the <thing>"
_SHORT_ON_OFF = re.compile(r"^((?:\{\w+\} )?[a-z']+(?: [a-z']+)?) (on|off)$")
_SET_TO = re.compile(r"^set (.+) to (.+)$")

# single words with a common stand-in (whole words only)
SYNONYMS: dict[str, list[str]] = {
    "tv": ["television", "telly"],
    "television": ["tv"],
    "goodnight": ["good night"],
    "good night": ["goodnight", "night night"],
}

MAX_VARIANTS = 6  # per phrase, so the recognizer's vocabulary stays small on a Pi


def variants(phrase: str) -> list[str]:
    """Other common ways of saying `phrase` (already lowercase), not including it. Slots ({room})
    are kept as they are."""
    p = re.sub(r"\s+", " ", phrase.strip())
    out: list[str] = []

    def add(v: str) -> None:
        v = re.sub(r"\s+", " ", v).strip()
        if v and v != p and v not in out:
            out.append(v)

    for pattern, reps in REWRITES:
        m = re.match(pattern, p, re.I)
        if not m:
            continue
        x = m.group(1)
        for r in reps:
            add(r.replace("{x}", x))
    m = _SET_TO.match(p.lower())
    if m:
        add(f"change {m.group(1)} to {m.group(2)}")
        add(f"put {m.group(1)} to {m.group(2)}")
        add(f"make {m.group(1)} {m.group(2)}")
    m = _SHORT_ON_OFF.match(p.lower())
    if m and not p.lower().startswith(("turn ", "switch ")):
        thing, state = m.groups()
        add(f"turn {state} the {thing}")
        add(f"turn the {thing} {state}")
        add(f"switch {state} the {thing}")
    for word, others in SYNONYMS.items():
        if re.search(rf"\b{re.escape(word)}\b", p, re.I):
            for o in others:
                add(re.sub(rf"\b{re.escape(word)}\b", o, p, flags=re.I))
    return out[:MAX_VARIANTS]


# ---------- polite filler around a command ----------
LEAD_FILLER = sorted([
    "please", "can you", "could you", "would you", "will you", "can you please", "could you please",
    "would you please", "will you please", "would you mind", "do you mind", "go ahead and",
    "i want you to", "i need you to", "i'd like you to", "i would like you to", "i'd like to",
    "let's", "just", "quickly", "um", "uh", "er", "so", "and", "and then", "then", "now", "okay",
    "ok", "alright", "hey", "yo", "oh", "actually", "also", "maybe", "kindly", "be a dear and",
    "do me a favor and", "do me a favour and", "mind",
], key=lambda s: -len(s))
TRAIL_FILLER = sorted([
    "please", "for me", "for me please", "now", "right now", "now please", "thanks", "thank you",
    "thanks a lot", "thank you very much", "if you can", "if you could", "would you", "will you",
    "could you", "real quick", "quickly", "asap", "already", "again", "too", "as well", "for us",
], key=lambda s: -len(s))
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
              "what about", "how about", "let me know", "guess what")
POLITE_REQUESTS = ("can you", "could you", "would you", "will you", "please", "i want you to",
                   "i need you to", "i'd like you to", "go ahead and", "would you mind", "do me a favor",
                   "do me a favour", "be a dear")
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
}


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
    if not strict and any(padded.startswith(p + " ") for p in NEED_STARTS):
        return "task"
    if any(padded.startswith(p + " ") for p in ASK_STARTS):
        return "question"
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
