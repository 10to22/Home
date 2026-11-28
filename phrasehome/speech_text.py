"""Text made ready for a voice to read out: what AI replies (especially local models) are full of and a
speech voice would read literally. The Mac voice says emoji by name ("smirking face", "party popper party
popper"), reads stage directions ("rolls eyes"), and stumbles on symbols.

Runs on everything just before it's turned into sound (voice.py), so the house's own lines, AI answers,
the radio host and the diary all get it. The text shown on the dashboard keeps its emoji.
"""

from __future__ import annotations

import re

# emoji and other pictures: faces, objects, flags, arrows, check marks, and the invisible joiners between them
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF\U00002190-\U000021FF"
                    "\U0001F1E6-\U0001F1FF‍️⃣⌀-⏿Ⓜ〰〽㊗㊙]+")
# "*rolls eyes*", "(sighs)", "[laughs]": stage directions, said aloud they're nonsense
_STAGE = (r"(?:sigh|laugh|chuckl|giggl|roll|smirk|wink|grin|groan|cough|clear|shrug|yawn|gasp|snort|scoff|mutter|"
          r"whisper|pause|beat|ahem|cringe|facepalm|nod|shake|wave|clap|smile|frown|blink|stare|glare|sniff|"
          r"eye|deadpan|dramatic|sarcastic|ironic|mock|in a |under (?:my|its) breath|robot|static|beep|ding)")
_STAGE_WRAPPED = re.compile(rf"(?:\*{{1,2}}|_)\s*{_STAGE}[^*_\n]{{0,40}}(?:\*{{1,2}}|_)", re.I)
_STAGE_PAREN = re.compile(rf"[(\[]\s*(?:{_STAGE}[^)\]\n]{{0,30}}|\w+ly)\s*[)\]]", re.I)
_UNITS = {"km": "kilometres", "kms": "kilometres", "kg": "kilos", "kgs": "kilos", "g": "grams", "ml": "millilitres",
          "l": "litres", "min": "minutes", "mins": "minutes", "hr": "hours", "hrs": "hours", "sec": "seconds",
          "secs": "seconds", "cm": "centimetres", "mm": "millimetres", "mph": "miles an hour", "kph": "k an hour",
          "km/h": "k an hour"}
_ABBREV = {r"\be\.g\.": "for example", r"\bi\.e\.": "that is", r"\betc\.": "and so on", r"\bvs\.?(?=\s)": "versus",
           r"\bapprox\.?(?=\s)": "about", r"\bw/o\b": "without", r"\bw/(?=\s)": "with"}


def first_pass(text: str) -> str:
    """What has to go before markdown is stripped (assistant.spoken runs this first): a model's private notes,
    <tags>, `code`, and stage directions while their *asterisks* still show what they are."""
    t = re.sub(r"<think>.*?(?:</think>|$)", " ", text, flags=re.S | re.I)
    t = re.sub(r"</?[a-zA-Z][^<>]{0,40}>", " ", t)
    t = re.sub(r"`([^`]*)`", lambda m: m.group(1).replace("_", " "), t)      # `lights_on` -> lights on
    t = _STAGE_WRAPPED.sub(" ", t)
    t = _STAGE_PAREN.sub(" ", t)
    return t


def for_speech(text: str) -> str:
    t = first_pass(text)
    t = re.sub(r"\[[a-z]+(?: [a-z]+)?\]", " ", t)                             # a [mood] tag left mid-text
    t = _EMOJI.sub(" ", t)
    t = re.sub(r"(?<=\w)\*{1,2}(?=\w)|\*{1,2}", "", t)                        # *emphasis* -> emphasis
    t = re.sub(r"(?<!\w)_(\w[^_]*)_(?!\w)", r"\1", t)
    t = re.sub(r"#+\s*", "", t)
    for rx, words in _ABBREV.items():
        t = re.sub(rx, words, t, flags=re.I)
    # numbers and symbols
    t = re.sub(r"(\d)\s*°\s*C\b", r"\1 degrees", t)
    t = re.sub(r"(\d)\s*°\s*F\b", r"\1 degrees Fahrenheit", t)
    t = re.sub(r"(\d)\s*°", r"\1 degrees", t)
    t = re.sub(r"(?<![\d/-])(\d{1,3})\s?[-–]\s?(\d{1,3})(?![\d/-])(?=\s*(?:degrees|percent|%|minutes?|mins?|"
               r"hours?|people|of|\w+s\b|[a-z]))", r"\1 to \2", t)          # 18-22 degrees -> 18 to 22
    t = re.sub(r"~\s?(?=\d)", "about ", t)
    t = re.sub(r"\s@\s", " at ", t)
    t = re.sub(r"\s&\s", " and ", t)
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    t = re.sub(r"(?<=\d)\s?(km/h|kms?|kgs?|ml|mins?|hrs?|secs?|cm|mm|mph|kph|g|l)\b(?!['\w])",
               lambda m: " " + _UNITS[m.group(1).lower()], t)
    t = re.sub(r"(?<=[\d\w])\s+/\s+(?=[$£€\d])", " or ", t)                   # $4.50 / £3.80
    t = re.sub(r"\s+[—–]\s+|\s*—\s*", ", ", t)                 # dashes -> a pause
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)
    t = re.sub(r"([,!?;:])\1+", r"\1", t)
    t = re.sub(r"\.{4,}", "...", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[,.;:\s]+", "", t)
    return t


ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "st", "mt", "prof", "sr", "jr", "vs", "etc", "e.g", "i.e", "approx",
                 "no", "fig", "ave", "rd", "a.m", "p.m", "u.s", "u.k"}


def sentence_end(buf: str, final: bool = False) -> int | None:
    """Where the first whole sentence in `buf` ends (for speaking while the answer is still being written),
    or None if there isn't one yet. Not after "Dr." or "e.g.", not inside "3.5", not at "..." when the
    sentence carries on in lower case. `final`: the text is complete, so a boundary at the very end counts."""
    for m in re.finditer(r"(?<=[.!?])[\"')\]]?(\s+)|\n+", buf):
        if m.group(0).startswith("\n"):
            return m.start()
        before = buf[: m.start()].rstrip("\"')]")
        word = re.findall(r"([A-Za-z.]+)\.$", before)
        if word and word[0].lower().rstrip(".") in ABBREVIATIONS:
            continue
        after = buf[m.end():]
        if before.endswith(".."):  # "out... finally!" carries on; "out... Then" doesn't
            if not after and not final:
                return None  # can't tell yet
            if after[:1].islower():
                continue
        return m.start() + (len(m.group(0)) - len(m.group(1) or "")) if m.group(1) else m.start()
    return None
