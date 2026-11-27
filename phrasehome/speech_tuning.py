"""Hearing you more precisely, and learning as it goes.

Sounds-like matching   when nothing matches word for word, the house compares how the words *sound*
                       ("turn on the kitchin lites" ~ "turn on the kitchen lights"). Settings: phonetic_match.
                       A sounds-like match scores a little lower, so risky commands still ask "did you say...?"
Learns from retries    said something it didn't get, then said it again differently and it worked? Within
                       20 seconds in the same room, it learns the first wording meant that command (and
                       which words it misheard, for the accent fixes). Nothing to do on the History page.
Learned words prime    words it has learned (accent fixes, taught sentences) go into Whisper's prompt and
Whisper                hotwords, so it expects them.
Better Whisper         beam search (settings: whisper_beam, 5 on a Mac), hotwords, and a stronger model
                       on a Mac with 16 GB or more (distil-medium.en). Set whisper_model to choose your own.

settings:
  recognizer: whisper     # much better than Vosk at whole sentences
  phonetic_match: true
  whisper_beam: 5
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

log = logging.getLogger("phrasehome.hearing")

_RULES = [
    (r"^kn", "n"), (r"^gn", "n"), (r"^pn", "n"), (r"^wr", "r"), (r"^ps", "s"), (r"^x", "s"), (r"^wh", "w"),
    (r"mb$", "m"), (r"ph", "f"), (r"gh(?=[^aeiou]|$)", ""), (r"ght", "t"), (r"ck", "k"), (r"sch", "sk"),
    (r"tch", "x"), (r"ch", "x"), (r"sh", "x"), (r"th", "0"), (r"dg(?=[eiy])", "j"), (r"c(?=[iey])", "s"),
    (r"c", "k"), (r"q", "k"), (r"x", "ks"), (r"z", "s"), (r"v", "f"), (r"d$", "t"), (r"(?<=[aeiou])y", ""),
    (r"(?<=[aeiou])w", ""), (r"(?<=.)h(?=[^aeiou]|$)", ""), (r"g(?=[iey])", "j"),
]


def phonetic_word(w: str) -> str:
    """A rough English sound key: "lights" ~ "lites", "kitchen" ~ "kitchin", "fone" ~ "phone"."""
    w = re.sub(r"[^a-z0-9]", "", w.lower())
    if not w or w.isdigit():
        return w
    for rx, rep in _RULES:
        w = re.sub(rx, rep, w)
    first, rest = w[0], re.sub(r"[aeiouy]", "", w[1:])
    out = first + rest
    return re.sub(r"(.)\1+", r"\1", out)


def phonetic(text: str) -> str:
    return " ".join(phonetic_word(w) for w in text.split())


# ---------------------------------------------------------------- learning from a second try
class RetryLearner:
    """A miss followed by a hit in the same room soon after: the miss meant the hit."""

    WINDOW = 20.0
    BAD = ("none", "unclear")

    def __init__(self, home) -> None:
        self.home = home
        self._miss: dict[str | None, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "learned_retries.json"

    def observe(self, entry: dict) -> dict | None:
        kind, room, heard = entry.get("kind"), entry.get("room"), str(entry.get("heard") or "")
        if entry.get("via") not in (None, "voice"):
            return None
        now = time.monotonic()
        if kind in self.BAD and heard:
            with self._lock:
                self._miss[room] = (now, heard)
            return None
        if kind != "command" or not entry.get("matched"):
            return None
        with self._lock:
            got = self._miss.pop(room, None)
        if not got or now - got[0] > self.WINDOW:
            return None
        miss = got[1]
        if not _related(miss, heard):
            return None
        from .understanding import Aliases

        try:
            item = Aliases(self.home.config).add(miss, str(entry["matched"]), entry.get("slots") or {})
        except Exception as e:
            log.debug("retry learning: %s", e)
            return None
        log.info("learned: %r means %s (it worked the second time)", miss, entry["matched"])
        self.home.events.log("hearing.learned", heard=miss, command=entry["matched"])
        try:
            rows = json.loads(self.path.read_text())
        except (OSError, ValueError):
            rows = []
        rows.append({"heard": miss, "then": heard, "command": entry["matched"], "time": time.strftime("%Y-%m-%d %H:%M")})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(rows[-500:], indent=1))
        return item


def _related(a: str, b: str) -> bool:
    from rapidfuzz import fuzz

    from .matcher import normalize

    a, b = normalize(a), normalize(b)
    if not a or not b or a == b:
        return False
    stop = {"the", "a", "to", "on", "off", "in", "my", "and", "please", "house", "unk"}
    shared = (set(a.split()) - stop) & (set(b.split()) - stop)
    return bool(shared) or fuzz.ratio(a, b) >= 55 or fuzz.ratio(phonetic(a), phonetic(b)) >= 65


def retry_for(home) -> RetryLearner:
    r = home.__dict__.get("_retry")
    if r is None:
        r = home.__dict__["_retry"] = RetryLearner(home)
    return r


# ---------------------------------------------------------------- words to listen for
def learned_words(config, limit: int = 40) -> list[str]:
    """Words and phrases the house has learned it should expect: accent-fix targets, taught sentences."""
    out: list[str] = []
    try:
        from .hearing_plus import AccentFixes

        out += list(dict.fromkeys(AccentFixes(config).rules().values()))
    except Exception:
        pass
    try:
        from .understanding import Aliases

        for a in reversed(Aliases(config).all()):
            if a.get("meant"):
                out.append(a["meant"])
    except Exception:
        pass
    seen: list[str] = []
    for w in out:
        if w and w not in seen:
            seen.append(w)
    return seen[:limit]


def whisper_beam(config) -> int:
    import sys

    n = int(getattr(config.settings, "whisper_beam", 0) or 0)
    return n if n > 0 else (5 if sys.platform == "darwin" else 2)


def memory_gb() -> float:
    try:
        import os

        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
    except (ValueError, OSError, AttributeError):
        try:
            import subprocess

            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) / 1e9
        except Exception:
            return 8.0
