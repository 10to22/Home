"""What the voice reads out: AI replies full of emoji, stage directions, markdown and symbols."""

import re

import pytest

from phrasehome.assistant import SentenceSpeaker, spoken
from phrasehome.speech_text import for_speech


def say(text):
    return for_speech(spoken(text))


@pytest.mark.parametrize("raw,want", [
    ("Done ✅ Lights off 🔒😏🎉🎉", "Done Lights off"),
    ("*rolls eyes* Try to remember this time.", "Try to remember this time."),
    ("(sarcastically) Oh great, another timer.", "Oh great, another timer."),
    ("No worries! (laughs) Enjoy.", "No worries! Enjoy."),
    ("<think>the user wants</think>Sure, I'll lock it.", "Sure, I'll lock it."),
    ("I *really* mean it.", "I really mean it."),
    ("The answer is `lights_on` & `room_lights_off`.", "The answer is lights on and room lights off."),
    ("It's 21°C and 65% humidity.", "It's 21 degrees and 65 percent humidity."),
    ("Pasta: 200g, ~10 mins @ 100°C.", "Pasta: 200 grams, about 10 minutes at 100 degrees."),
    ("$4.50 / £3.80, e.g. at Coles.", "$4.50 or £3.80, for example at Coles."),
    ("Highs of 18-22°C.", "Highs of 18 to 22 degrees."),
    ("Mate... seriously?!", "Mate... seriously?!"),
    ("**Plan:**\n1. Wash up\n2. Bins", "Plan: Wash up. Bins"),
])
def test_ai_replies_read_out_properly(raw, want):
    assert say(raw) == want


def test_numbers_that_arent_ranges_are_left_alone():
    out = for_speech("It's 2026-11-28, call 555-1234, it's 7:30pm on 12/5.")
    assert "2026-11-28" in out and "555-1234" in out and "12/5" in out


def test_every_house_line_keeps_all_its_words():
    from phrasehome import house_lines, voice

    lines = set()

    def walk(x):
        if isinstance(x, str):
            lines.add(x)
        elif isinstance(x, list):
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v.get("text", "") if isinstance(v, dict) and "text" in v else v)

    for mod in (voice, house_lines):
        for name in dir(mod):
            obj = getattr(mod, name)
            if name.isupper() and isinstance(obj, (dict, list)):
                walk(obj)
    lines = {t for t in lines if " " in t and not re.search(r"[=|]|afftfilt", t)}
    assert len(lines) > 500
    for t in lines:
        before = set(re.findall(r"[a-z']+", t.lower()))
        after = set(re.findall(r"[a-z']+", for_speech(t).lower()))
        assert before <= after, (t, before - after)


class Voice:
    def __init__(self):
        self.out = []

    def speak(self, text, room=None, **kw):
        self.out.append(text)


@pytest.mark.parametrize("text,want", [
    ("Dr. Smith said it's 3.5 km away, e.g. a walk. Bring a coat.",
     ["Dr. Smith said it's 3.5 km away, e.g. a walk.", "Bring a coat."]),
    ("Version 2.0 is out... finally! Wait.", ["Version 2.0 is out... finally!", "Wait."]),
    ("Mr. and Mrs. Jones live on Oak St. next door. Nice.", ["Mr. and Mrs. Jones live on Oak St. next door.", "Nice."]),
])
def test_answers_are_spoken_a_whole_sentence_at_a_time(text, want):
    v = Voice()
    sp = SentenceSpeaker(v, None, max_sentences=9, max_chars=2000)
    for i in range(0, len(text), 3):  # as it streams in
        sp.feed(text[i:i + 3])
    sp.finish()
    assert v.out == want


def test_the_voice_gets_the_clean_text(tmp_path, monkeypatch):
    from phrasehome import voice as vmod
    from tests.test_voice_style import make_cfg

    sent = []
    monkeypatch.setattr(vmod.sys, "platform", "linux")
    monkeypatch.setattr(vmod.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "espeak-ng" else None)

    def run(cmd, input=None, **kw):
        sent.append(cmd[-1] if input is None else input.decode())
        out = next((cmd[i + 1] for i, c in enumerate(cmd) if c in ("-w", "--output_file")), None)
        if out:
            open(out, "wb").write(b"RIFF" + b"\x00" * 60)
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr(vmod.subprocess, "run", run)
    v = vmod.Voice(make_cfg(tmp_path, "{engine: espeak}"))
    v.synthesize("Lights off 🔒 *rolls eyes* Enjoy 😏", v.base)
    assert sent and "Lights off Enjoy" in sent[-1] and "🔒" not in sent[-1]


# ---------------------------------------------------------------- the Mac voice, speed, warming up
def test_mac_say_never_obeys_a_reply_or_mistakes_it_for_an_option(tmp_path, monkeypatch):
    from phrasehome import voice as vmod
    from tests.test_voice_style import make_cfg

    ran = []
    monkeypatch.setattr(vmod.sys, "platform", "darwin")
    monkeypatch.setattr(vmod.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "say" else None)
    monkeypatch.setattr(vmod, "mac_voices", lambda: [])

    def run(cmd, **kw):
        ran.append(cmd)
        out = cmd[cmd.index("-o") + 1]
        open(out, "wb").write(b"RIFF" + b"\x00" * 60)
        return type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    monkeypatch.setattr(vmod.subprocess, "run", run)
    v = vmod.Voice(make_cfg(tmp_path, "{engine: say}"))
    v.synthesize("-5 degrees tonight. [[volm 0]] Rug up.", v.base)
    said = ran[-1][-1]
    assert not said.startswith("-") and "-5 degrees" in said and "volm" not in said


def test_voice_speed_is_tracked(tmp_path):
    from phrasehome.voice import Voice
    from tests.test_voice_style import make_cfg

    v = Voice(make_cfg(tmp_path, "{engine: none}"))
    for made, s in ((True, 0.8), (False, 0.001), (True, 1.2)):
        v._timed("x", "say", s)
    sp = v.speed()
    assert sp == {"lines": 3, "cached": 1, "made": 2, "average": 1.0, "slowest": 1.2}


def test_warm_up_makes_the_fixed_lines_first_and_waits_while_it_talks(tmp_path, monkeypatch):
    from phrasehome import audio_out
    from phrasehome.voice import Voice
    from tests.test_voice_style import make_cfg

    v = Voice(make_cfg(tmp_path, "{engine: none}"))
    lines = v.warm_lines()
    assert lines and all("{" not in t for _, t, _ in lines)
    keys = [k for k, _, _ in lines]
    assert "listening" in keys and "time_now" not in keys  # "It's {time}." can't be made ahead
    made = []
    monkeypatch.setattr(v, "synthesize", lambda text, vv=None, emotion=None: made.append(text) or None)
    monkeypatch.setattr(type(v), "router", property(lambda self: type("R", (), {"rooms": lambda s: set()})()))
    busy = {"n": 2}

    def local_busy():
        busy["n"] -= 1
        return busy["n"] > 0
    monkeypatch.setattr(audio_out, "local_busy", local_busy)
    monkeypatch.setattr("phrasehome.voice.time.sleep", lambda s: None)
    v.warm(limit=5)
    assert len(made) == 5 and busy["n"] <= 0  # it waited for the house to stop talking first
    made.clear()
    v.warm(limit=5, stop=lambda: True)
    assert made == []
