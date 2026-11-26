"""When the house is paying attention, and how it learns when it got that wrong.

Name anywhere   "what's the time, house?" and "lights off, home" count as said to the house: the name at
                the end works as well as at the start ("I love this house" and "welcome home" don't).
                assistant: {name_anywhere: false} for the start only.
Stop listening  "house, stop listening" (for half an hour) or "...for 20 minutes / 2 hours": the AI stays
                out of it in that room. Commands still work. "house, start listening" or "you can
                listen again" ends it early.
Not for you     "I wasn't talking to you", "that wasn't for you" just after an answer: the house stops
                talking and remembers that sentence wasn't for it.
Learning        sentences the house got wrong are kept (data/intent_examples.json): the AI's quick check
                sees them as examples, and the same sentence again is decided without asking it.
                Ignored, then said again with the name within 30 seconds: it was for the house after all.
                assistant: {learn: false} turns this off.
Speaker rules   assistant: {no_name_needed: [sam]}: Sam doesn't need the name in Sam's own rooms (still
                only tasks and questions get an answer). voice_id: {strangers_need_name: true}: voices the
                house doesn't know (guests, the TV) can't run commands without the name, except safety
                ones (shut up, stop the alarm, fire).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("phrasehome.attention")

# ---------- the name at the end ----------
# "go home", "welcome home", "this house": the name is just a word there, not someone talking to the house
NOT_VOCATIVE = {
    "go", "goes", "going", "gone", "went", "come", "comes", "coming", "came", "get", "getting", "got", "head",
    "heading", "drive", "driving", "drove", "walk", "walking", "bring", "brought", "take", "took", "call",
    "back", "at", "to", "from", "my", "our", "your", "their", "his", "her", "this", "that", "the", "a", "an",
    "sweet", "welcome", "whole", "in", "out", "around", "inside", "outside", "i'm", "im", "we're", "were",
    "you're", "they're", "he's", "she's", "stay", "staying", "work", "leave", "left", "safe", "new", "old",
    "big", "own", "nice", "lovely", "clean", "dirty", "of", "for", "full", "party", "tree", "white", "open",
    "nursing", "mobile", "rest", "funeral", "green", "hen", "boat", "guest", "dream", "haunted", "beach",
}


def name_at_end(words: list[str], names: list[str]) -> str | None:
    """`words` end with one of `names` (said to the house): the rest, else None. Only when what comes
    before is clearly a request or a question ("turn off the lights, house"), so "I love this house" and
    "time to go home" don't count."""
    from .wordbank import quick_intent

    for name in sorted(names, key=len, reverse=True):
        n = name.split()
        if len(words) <= len(n) or words[-len(n):] != n:
            continue
        before = words[: -len(n)]
        while before and before[-1] in ("hey", "please", "ok", "okay"):
            before = before[:-1]
        if len(before) < 2 or before[-1] in NOT_VOCATIVE:
            return None
        rest = " ".join(before)
        if quick_intent(rest, strict=True):
            return rest
        return None
    return None


# ---------- stop listening ----------
_NUM = None


def _number(word: str) -> int | None:
    global _NUM
    if word.isdigit():
        return int(word)
    if _NUM is None:
        from .clock import number_words

        _NUM = {number_words(n): n for n in range(1, 241)} | {"a": 1, "an": 1, "half an": 0, "a couple of": 2}
    return _NUM.get(word)


_STOP = re.compile(r"^(?:please )?(?:stop|quit) listening(?: (?:to us|to me|for now))?"
                   r"(?: for (?P<n>.+?) (?P<unit>minutes?|mins?|hours?|hrs?))?(?: please)?$")
_STOP_OTHER = re.compile(r"^(?:give us some privacy|leave us alone|take a break|go away for a bit|"
                         r"stay out of it|mind your own business)(?: for (?P<n>.+?) (?P<unit>minutes?|hours?))?$")
_START = re.compile(r"^(?:please )?(?:start listening|you can listen again|listen again|i need you|"
                    r"come back|you can come back|resume listening|we're done|okay you can listen)(?: please)?$")
DEFAULT_QUIET_MINUTES = 30
QUIET_WORDS = sorted(set("stop quit start listening listen again for minutes minute hours hour half an "
                         "resume you can come back i need give us some privacy leave alone take a break "
                         "stay out of it mind your own business we're done okay now to me".split()))


def quiet_request(said: str) -> tuple[str, float] | None:
    """("stop", minutes) or ("start", 0) for "stop listening for 20 minutes" / "start listening"."""
    s = said.strip()
    if _START.match(s):
        return ("start", 0.0)
    m = _STOP.match(s) or _STOP_OTHER.match(s)
    if not m:
        return None
    if not m.group("n"):
        return ("stop", float(DEFAULT_QUIET_MINUTES))
    n_text = m.group("n")
    if n_text in ("half an", "half a"):
        return ("stop", 30.0)
    n = _number(n_text)
    if n is None:
        return ("stop", float(DEFAULT_QUIET_MINUTES))
    hours = m.group("unit").startswith(("h", "hr"))
    return ("stop", float(n) * (60 if hours else 1))


class Quiet:
    """Rooms where the AI stays out of it for a while ("stop listening"). Commands still work."""

    def __init__(self) -> None:
        self.until: dict[str | None, float] = {}

    def stop(self, room: str | None, minutes: float) -> None:
        self.until[room] = time.monotonic() + minutes * 60

    def start(self, room: str | None) -> bool:
        return self.until.pop(room, None) is not None

    def on(self, room: str | None) -> bool:
        end = self.until.get(room)
        if end is None:
            return False
        if time.monotonic() > end:
            del self.until[room]
            return False
        return True


def say_minutes(minutes: float) -> str:
    m = int(round(minutes))
    if m % 60 == 0 and m >= 60:
        h = m // 60
        return "an hour" if h == 1 else f"{h} hours"
    return "a minute" if m == 1 else f"{m} minutes"


# ---------- "I wasn't talking to you" ----------
NOT_FOR_YOU = re.compile(
    r"^(?:no |sorry |oops )?(?:i wasn't talking to you|i was not talking to you|we weren't talking to you|"
    r"we were not talking to you|that wasn't for you|that was not for you|not you|not talking to you|"
    r"wasn't talking to you|nobody was talking to you|i didn't ask you|i wasn't asking you|"
    r"i was talking to \w+)(?: house| home)?$")
NOT_FOR_YOU_WORDS = sorted(set("i we wasn't weren't was were not talking to you that for nobody didn't ask "
                               "asking sorry no oops".split()))


def not_for_you(said: str) -> bool:
    return bool(NOT_FOR_YOU.match(said.strip()))


# ---------- learning: which sentences were for the house ----------
class IntentExamples:
    """Sentences the house misjudged, and what they really were (data/intent_examples.json)."""

    KEEP = 200

    def __init__(self, home) -> None:
        self.home = home
        self.enabled = bool((home.config.assistant or {}).get("learn", True))
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "intent_examples.json"
        self._lock = threading.Lock()

    def all(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def add(self, said: str, for_house: bool, why: str = "") -> None:
        from .matcher import normalize

        said = normalize(said)
        if not self.enabled or not said:
            return
        with self._lock:
            items = [e for e in self.all() if e.get("said") != said]
            items.append({"said": said, "for_house": bool(for_house), "why": why,
                          "when": time.strftime("%Y-%m-%d %H:%M:%S")})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(items[-self.KEEP:], indent=1), encoding="utf-8")
        log.info("learned: %r was %sfor the house (%s)", said, "" if for_house else "not ", why)
        self.home.events.log("assistant.learned", said=said, for_house=bool(for_house), why=why)

    def lookup(self, said: str) -> bool | None:
        from .matcher import normalize

        if not self.enabled:
            return None
        said = normalize(said)
        for e in reversed(self.all()):
            if e.get("said") == said:
                return bool(e.get("for_house"))
        return None

    def prompt(self, n: int = 15) -> str:
        if not self.enabled:
            return ""
        items = self.all()[-n:]
        if not items:
            return ""
        lines = "\n".join(f"- \"{e['said']}\": {'for the house' if e['for_house'] else 'neither'}" for e in items)
        return f"\nPeople in this home said these before, and this is what they meant:\n{lines}\n"


def examples_for(home) -> IntentExamples:
    ex = home.__dict__.get("_intent_examples")
    if ex is None:
        ex = home.__dict__["_intent_examples"] = IntentExamples(home)
    return ex


# ---------- speaker rules ----------
def no_name_needed(assistant, person: str | None, room: str | None) -> bool:
    """assistant: {no_name_needed: [sam]}: Sam, recognized by voice, in one of Sam's own rooms."""
    if not person or not room:
        return False
    who = [str(p).lower() for p in (assistant.home.config.assistant or {}).get("no_name_needed") or []]
    if not who:
        return False
    inst = assistant.home.instances.all.get(person)
    if inst is None:
        return False
    if person.lower() not in who and str(inst.name).lower() not in who:
        return False
    return room.lower() in [r.lower() for r in inst.rooms]


def strangers_need_name(home) -> bool:
    vid = getattr(home, "voice_id", None)
    return bool(vid is not None and (getattr(vid, "cfg", None) or {}).get("strangers_need_name")
                and getattr(vid, "prints", None))


def is_safety_command(cmd) -> bool:
    """Commands anyone may give, known voice or not: shut up, stop the alarm, fire, privacy off."""
    a = cmd.action
    b = a.get("backend")
    if a.get("hush"):
        return True
    if b == "alarm" and str(a.get("alarm")).lower() in ("stop", "snooze"):
        return True
    if b == "routine" and str(a.get("routine")) in ("fire_mode", "panic"):
        return True
    return b == "privacy" and str(a.get("privacy")).lower() in ("off", "false")
