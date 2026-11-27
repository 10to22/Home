"""Turn recognized speech into a command match."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz, process

from .config import Command

_SLOT_RE = re.compile(r"\{(\w+)\}")


def normalize(text: str) -> str:
    """Lowercase words only. Keeps letters of every language (é, ñ, ü, ...)."""
    text = text.casefold().replace("[unk]", " unk ")
    text = text.replace("\u2019", "'")
    text = re.sub(r"[^\w' ]+|_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Candidate:
    text: str  # normalized, slot values filled in
    command: Command
    slots: dict[str, str] = field(default_factory=dict)  # slot name -> value

    @property
    def words(self) -> list[str]:
        return self.text.split()


@dataclass
class Match:
    command: Command
    phrase: str
    slots: dict[str, str]
    score: float
    heard: str
    room: str | None = None  # room of the mic that heard it
    person: str | None = None  # who said it, if their voice was recognized
    confidence: float = 1.0
    notify: Callable[[str], None] | None = None  # feedback to the mic: "matched"/"done"/"failed"
    via: str = "voice"  # voice | typed | did-you-mean
    confirmed: bool = False  # a risky command someone said yes to


def expand(commands: list[Command], slots: dict[str, dict[str, str]],
           lang: str | None = None, variants: bool = False, limit: int | None = None) -> list[Candidate]:
    """Expand every phrase template ("turn on the {room} lights") into concrete phrases.
    variants: also the usual other ways of saying each phrase and other names for rooms (wordbank.py),
    English only; at most `limit` other wordings per phrase. A variant never replaces a phrase written
    in the config, for this command or any other."""
    out = _expand(((cmd, t) for cmd in commands for t in cmd.phrases_for(lang)), slots)
    if not variants:
        return out
    from .wordbank import MAX_VARIANTS, place_names
    from .wordbank import variants as other_ways

    n = MAX_VARIANTS if limit is None else limit
    written = {c.text for c in out}
    more_slots = place_names(slots)
    extra = [c for c in _expand(((cmd, v) for cmd in commands for t in cmd.phrases_for(lang)
                                 for v in [t] + other_ways(t, n)), more_slots) if c.text not in written]
    seen: set[str] = set()
    return out + [c for c in extra if not (c.text in seen or seen.add(c.text))]


def _expand(templates, slots: dict[str, dict[str, str]]) -> list[Candidate]:
    out: list[Candidate] = []
    for cmd, template in templates:
        names = _SLOT_RE.findall(template)
        for n in names:
            if n not in slots:
                raise ValueError(f"phrase '{template}' uses undefined slot '{{{n}}}'")
        choices = [list(slots[n].items()) for n in names]
        for combo in itertools.product(*choices):
            text = template
            values = {}
            for n, (spoken, value) in zip(names, combo):
                text = text.replace("{" + n + "}", spoken, 1)
                values[n] = value
            out.append(Candidate(normalize(text), cmd, values))
    return out


def _same_sounding(heard: list[str], words: list[str]) -> bool:
    """Word by word: the same, or spelled close and sounding alike ("kitchin" ~ "kitchen", "lites" ~
    "lights"), never two different words that happen to share consonants ("cozy" vs "quiz"). At most half
    the words (and at least two) may differ."""
    from .speech_tuning import phonetic_word

    diff, most = 0, max(2, (len(words) + 1) // 2)
    for a, b in zip(heard, words):
        if a == b:
            continue
        diff += 1
        if diff > most or phonetic_word(a) != phonetic_word(b) or fuzz.ratio(a, b) < 50:
            return False
    return diff > 0


def vocabulary(candidates: list[Candidate]) -> list[str]:
    """Grammar for Vosk: every phrase, every individual word, plus [unk] for everything else."""
    phrases = sorted({c.text for c in candidates})
    words = sorted({w for c in candidates for w in c.words})
    return phrases + words + ["[unk]"]


class Matcher:
    def __init__(
        self,
        candidates: list[Candidate],
        mode: str = "utterance",
        threshold: float = 85.0,
        block_words: list[str] | None = None,
        phonetic: bool = True,
        synonyms: bool = True,
    ) -> None:
        self.candidates = candidates
        self.synonyms = synonyms  # telly = tv = television on both sides (wordbank.same_words)
        self._texts = [self._same(c.text) for c in candidates]
        self.phonetic = phonetic
        self._sounds: list[str] | None = None  # how each candidate sounds (speech_tuning.phonetic), made on first use
        self.mode = mode
        self.threshold = threshold
        self.block = [normalize(b) for b in (block_words or []) if normalize(b)]

    def _blocked(self, heard: str, phrase: str) -> bool:
        h, p = f" {heard} ", f" {phrase} "
        return any(f" {b} " in h and f" {b} " not in p for b in self.block)

    def _score(self, heard_words: list[str], cand: Candidate) -> float:
        heard = " ".join(heard_words)
        if self.mode == "utterance":
            return fuzz.ratio(heard, cand.text)
        # contains: best-scoring window of the same word length (±1) inside the utterance
        n = len(cand.words)
        best = 0.0
        for size in (n - 1, n, n + 1):
            if size < 1 or size > len(heard_words):
                continue
            for i in range(len(heard_words) - size + 1):
                window = " ".join(heard_words[i : i + size])
                best = max(best, fuzz.ratio(window, cand.text))
        return best

    def _same(self, text: str) -> str:
        if not getattr(self, "synonyms", True):
            return text
        from .wordbank import same_words

        return same_words(text)

    def match(self, text: str) -> Match | None:
        from .wordbank import strip_filler

        heard = normalize(text)
        words = [w for w in heard.split() if not (self.mode == "contains" and w == "unk")]
        if not words:
            return None
        # "could you please lock the doors for me" is scored as "lock the doors" too
        bare = strip_filler(" ".join(words)).split()
        tries = [words] if bare == words else [words, bare]
        if self.mode == "utterance" and self.synonyms:
            tries = [self._same(" ".join(w)).split() for w in tries]
        best: tuple[float, int, Candidate] | None = None
        if self.mode == "utterance":  # all at once in C: thousands of wordings stay instant
            scores: dict[int, float] = {}
            for w in tries:
                for _, sc, i in process.extract(" ".join(w), self._texts, scorer=fuzz.ratio,
                                                score_cutoff=self.threshold, limit=None):
                    scores[i] = max(sc, scores.get(i, 0.0))
            for i in sorted(scores):
                cand = self.candidates[i]
                key = (scores[i], len(cand.text), cand)
                if best is None or key[:2] > best[:2]:
                    best = key
        else:
            for cand in self.candidates:
                score = max(self._score(w, cand) for w in tries)
                if score < self.threshold:
                    continue
                key = (score, len(cand.text), cand)
                if best is None or key[:2] > best[:2]:
                    best = key
        if best is None and self.phonetic and self.mode == "utterance":
            best = self._sounds_like(tries)
        if best is None:
            return None
        score, _, cand = best
        if self._blocked(heard, cand.text):
            return None
        return Match(cand.command, cand.text, cand.slots, score, heard)

    def _sounds_like(self, tries: list[list[str]]):
        """No word-for-word match: the candidate that sounds the same, scored a little lower (so risky
        commands still get "did you say ...?")."""
        from .speech_tuning import phonetic

        if self._sounds is None:
            self._sounds = [phonetic(t) for t in self._texts]
        best = None
        for w in tries:
            if len(w) < 2:
                continue  # one word sounds like too many things
            said = phonetic(" ".join(w))
            for _, sc, i in process.extract(said, self._sounds, scorer=fuzz.ratio,
                                            score_cutoff=max(self.threshold, 90), limit=5):
                cand = self.candidates[i]
                cwords = self._texts[i].split()
                if len(cwords) != len(w) or not _same_sounding(w, cwords):
                    continue
                key = (min(sc - 4, 90.0), len(cand.text), cand)  # never "sure": risky ones still ask
                if best is None or key[:2] > best[:2]:
                    best = key
        return best

    def near(self, text: str, floor: float = 60.0, n: int = 5) -> list[tuple[float, Candidate]]:
        """Commands that almost matched (between `floor` and the threshold), best first, one per command."""
        heard = normalize(text)
        words = [w for w in heard.split() if w != "unk"]
        if not words or len(words) > 10:
            return []
        best: dict[str, tuple[float, Candidate]] = {}
        for cand in self.candidates:
            score = self._score(words, cand)
            if floor <= score < self.threshold and score > best.get(cand.command.name, (0, None))[0]:
                if not self._blocked(heard, cand.text):
                    best[cand.command.name] = (score, cand)
        return sorted(best.values(), key=lambda x: -x[0])[:n]
