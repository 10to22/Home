"""Teach the house its vocabulary: hundreds of your phrases, said by many voices, squeezed through mp3,
run through the recognizer, and every mishearing it makes again and again is learned.

    phrasehome train-hearing                          # every voice this computer has (Mac voices, Piper, eSpeak)
    phrasehome train-hearing --voices ~/piper-voices  # a folder of Piper voices (.onnx); multi-speaker ones too
    phrasehome train-hearing --phrases 600 --speakers 12 --noise music.mp3 --keep
    phrasehome train-hearing --dry                    # measure only, learn nothing
    phrasehome train-hearing --background party.mp3   # long real audio (music, TV, talk): what would fire?

What it learns (into data/accent.json and data/aliases.json, the same places the History page teaches):
  word fixes      the recognizer keeps writing X where you said Y, across different voices, and X isn't a word
                  your commands use: X becomes Y everywhere ("kitten" -> "kitchen").
  sentence fixes  a whole command comes out as something that runs nothing: that wording now means the command.
It never learns anything that would make another command fire, and it lists:
  clashes         wordings that came out as a *different* command (rename one of them),
  missing words   words in your commands that the speech model doesn't know (Vosk can never hear them;
                  Whisper can, or pick another word).
The report (data/train-hearing/report.md) has accuracy before and after, per voice and per recognizer.
"""

from __future__ import annotations

import json
import logging
import math
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

log = logging.getLogger("phrasehome.train")
RATE = 16000


# ---------------------------------------------------------------- audio helpers
def to_pcm(data_or_path, rate: int = RATE) -> bytes:
    """Any audio (mp3, m4a, ogg, wav...) to 16 kHz mono 16-bit, with ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is needed (brew install ffmpeg)")
    src = ["-i", str(data_or_path)] if isinstance(data_or_path, (str, Path)) else ["-i", "pipe:0"]
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *src, "-ac", "1", "-ar", str(rate), "-f", "s16le", "pipe:1"],
                       input=None if isinstance(data_or_path, (str, Path)) else data_or_path,
                       capture_output=True, timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[-300:])
    return r.stdout


def wav_bytes(pcm: bytes, rate: int = RATE) -> bytes:
    import io

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def mp3_round_trip(pcm: bytes, kbps: int = 48) -> tuple[bytes, bytes]:
    """(the mp3, the audio decoded back from it): what a phone recording or a stream does to a voice."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
                        "-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-f", "mp3", "pipe:1"],
                       input=pcm, capture_output=True, timeout=60)
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[-300:])
    return r.stdout, to_pcm(r.stdout)


def mix(pcm: bytes, noise: np.ndarray | None, snr_db: float, rng: random.Random) -> bytes:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if not len(x):
        return pcm
    if noise is None or not len(noise):
        n = np.array([rng.gauss(0, 1) for _ in range(min(len(x), 4000))], dtype=np.float32)
        n = np.resize(n, len(x))
    else:
        start = rng.randrange(0, max(1, len(noise) - len(x)))
        n = np.resize(noise[start:start + len(x)], len(x)).astype(np.float32)
    ps, pn = float(np.mean(x * x)) + 1e-9, float(np.mean(n * n)) + 1e-9
    n *= math.sqrt(ps / (pn * 10 ** (snr_db / 10)))
    return np.clip(x + n, -32768, 32767).astype(np.int16).tobytes()


def pad(pcm: bytes, seconds: float = 0.35) -> bytes:
    z = b"\x00\x00" * int(RATE * seconds)
    return z + pcm + z


# ---------------------------------------------------------------- voices
class Voice:
    name = "voice"

    def say(self, text: str, rate: float = 1.0) -> bytes:  # 16 kHz pcm
        raise NotImplementedError


class PiperVoice_(Voice):
    def __init__(self, onnx: Path, speaker: int | None = None) -> None:
        from piper import PiperVoice

        self.path = onnx
        self.voice = PiperVoice.load(str(onnx))
        self.speaker = speaker
        stem = onnx.stem.replace(".onnx", "")
        self.name = f"piper:{stem}" + (f"#{speaker}" if speaker is not None else "")

    def say(self, text: str, rate: float = 1.0) -> bytes:
        import io

        from piper import SynthesisConfig

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self.voice.synthesize_wav(text, w, syn_config=SynthesisConfig(speaker_id=self.speaker, length_scale=1 / rate))
        return to_pcm(buf.getvalue())


class MacVoice(Voice):
    def __init__(self, name: str) -> None:
        self.voice, self.name = name, f"mac:{name}"

    def say(self, text: str, rate: float = 1.0) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "s.aiff"
            subprocess.run(["say", "-v", self.voice, "-r", str(int(185 * rate)), "-o", str(f), text], check=True, timeout=60)
            return to_pcm(f)


class EspeakVoice(Voice):
    def __init__(self, variant: str) -> None:
        self.variant, self.name = variant, f"espeak:{variant}"

    def say(self, text: str, rate: float = 1.0) -> bytes:
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        r = subprocess.run([exe, "-v", self.variant, "-s", str(int(160 * rate)), "--stdout", text], capture_output=True,
                           timeout=60, check=True)
        return to_pcm(r.stdout)


def find_voices(folders: Iterable[Path] = (), speakers: int = 6, rng: random.Random | None = None,
                espeak: bool = True) -> list[Voice]:
    """Every voice this computer can use: Mac voices (English ones), Piper voices in the folders (several
    speakers from multi-speaker ones), and a few eSpeak accents as a last resort."""
    rng = rng or random.Random(1)
    out: list[Voice] = []
    if sys.platform == "darwin" and shutil.which("say"):
        try:
            lines = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout.splitlines()
            for line in lines:
                m = re.match(r"^(.+?)\s{2,}(en_[A-Z]{2})\b", line)
                if m and not re.search(r"Bad News|Bells|Boing|Bubbles|Cellos|Good News|Jester|Organ|Superstar|Trinoids|"
                                       r"Whisper|Wobble|Zarvox|Bahh|Albert|Fred|Junior|Ralph", m.group(1)):
                    out.append(MacVoice(m.group(1).strip()))
        except Exception as e:
            log.info("Mac voices: %s", e)
    for folder in folders:
        for onnx in sorted(Path(folder).expanduser().rglob("*.onnx")):
            try:
                cfg = json.loads(Path(str(onnx) + ".json").read_text())
                if not str(cfg.get("language", {}).get("code", cfg.get("espeak", {}).get("voice", "en"))).startswith("en"):
                    continue
                n = int(cfg.get("num_speakers", 1) or 1)
                if n > 1:
                    for sid in rng.sample(range(n), min(speakers, n)):
                        out.append(PiperVoice_(onnx, sid))
                else:
                    out.append(PiperVoice_(onnx))
            except Exception as e:
                log.info("Piper voice %s: %s", onnx, e)
    if espeak and (shutil.which("espeak-ng") or shutil.which("espeak")) and len(out) < 4:
        out += [EspeakVoice(v) for v in ("en-us", "en-gb", "en-us+f3", "en-gb-x-rp")]
    return out


# ---------------------------------------------------------------- what to say
def phrases(config, n: int = 300, seed: int = 1) -> list[dict]:
    """Your commands, every way of saying them (the word bank), slots filled in; at least one per command,
    the written phrases first, then the variants; plus the names and rooms in "house, ..." sentences."""
    from .matcher import expand

    rng = random.Random(seed)
    written = {c.text for c in expand(config.commands, config.slots)}
    by_cmd: dict[str, list] = defaultdict(list)
    for c in expand(config.commands, config.slots, variants=True, limit=40):
        by_cmd[c.command.name].append(c)
    picks: list[dict] = []
    names = list(by_cmd)
    rng.shuffle(names)
    for name in names:  # one written phrase per command first
        pool = [c for c in by_cmd[name] if c.text in written] or by_cmd[name]
        c = rng.choice(pool)
        picks.append({"text": c.text, "command": name, "slots": c.slots})
    rest = [c for cs in by_cmd.values() for c in cs]
    rng.shuffle(rest)
    seen = {p["text"] for p in picks}
    for c in rest:
        if len(picks) >= n:
            break
        if c.text not in seen:
            seen.add(c.text)
            picks.append({"text": c.text, "command": c.command.name, "slots": c.slots})
    wake = ((config.assistant or {}).get("names") or ["house"])[0]
    people = [i.name for i in config.instances if not i.main]
    rooms = sorted({r for i in config.instances for r in i.rooms})
    extra = [f"{wake} tell {p} dinner's ready" for p in people] + [f"{wake} what's on in the {r}" for r in rooms[:6]]
    for e in extra[: max(0, n - len(picks)) or len(extra)]:
        picks.append({"text": e, "command": "(ai)", "slots": {}})
    return picks[: max(n, len(names))]


# ---------------------------------------------------------------- the run
class Trainer:
    def __init__(self, config, voices: list[Voice], hear: dict[str, Callable[[bytes], str]], noise: np.ndarray | None = None,
                 out: Path | None = None, seed: int = 1, keep: bool = False, show=print) -> None:
        self.config, self.voices, self.hear, self.noise = config, voices, hear, noise
        self.rng = random.Random(seed)
        self.out = out or Path(config.resolve_path(config.data_dir)) / "train-hearing"
        self.keep, self.show = keep, show
        self.rows: list[dict] = []

    def matcher(self):
        from .matcher import Matcher
        from .understanding import candidates_with_aliases

        s = self.config.settings
        return Matcher(candidates_with_aliases(self.config), s.match_mode, s.match_threshold, s.block_words,
                       phonetic=bool(getattr(s, "phonetic_match", True)))

    def make_clips(self, items: list[dict], per_phrase: int = 2) -> list[dict]:
        clips = []
        if self.keep:
            (self.out / "clips").mkdir(parents=True, exist_ok=True)
        t0 = time.monotonic()
        for i, item in enumerate(items):
            for voice in self.rng.sample(self.voices, min(per_phrase, len(self.voices))):
                rate = self.rng.choice([0.9, 1.0, 1.0, 1.1, 1.2])
                try:
                    pcm = voice.say(item["text"], rate)
                except Exception as e:
                    log.info("%s couldn't say %r: %s", voice.name, item["text"], e)
                    continue
                cond = "clean"
                if self.rng.random() < 0.5:
                    snr = self.rng.choice([20, 15, 10])
                    pcm = mix(pcm, self.noise, snr, self.rng)
                    cond = f"noise {snr} dB"
                mp3, pcm = mp3_round_trip(pad(pcm), self.rng.choice([32, 48, 64]))
                clip = {**item, "voice": voice.name, "rate": rate, "cond": cond, "pcm": pcm}
                if self.keep:
                    f = self.out / "clips" / f"{len(clips):04d}.mp3"
                    f.write_bytes(mp3)
                    clip["file"] = f.name
                clips.append(clip)
            if (i + 1) % 50 == 0:
                self.show(f"  made {len(clips)} clips ({i + 1}/{len(items)} phrases, {time.monotonic() - t0:.0f} s)")
        return clips

    def score(self, clips: list[dict], label: str) -> dict:
        from .hearing_plus import AccentFixes
        from .matcher import normalize
        from .speechtest import word_error_rate

        fixes = AccentFixes(self.config)
        m = self.matcher()
        res: dict[str, dict] = {}
        for name, fn in self.hear.items():
            rows, ok, errs, secs = [], 0, [], 0.0
            for c in clips:
                if "heard" not in c.setdefault("by", {}).setdefault(name, {}):
                    t0 = time.monotonic()
                    c["by"][name]["heard"] = normalize(fn(c["pcm"]))
                    c["by"][name]["secs"] = time.monotonic() - t0
                heard = fixes.apply(c["by"][name]["heard"])
                secs += c["by"][name]["secs"]
                hit = m.match(heard)
                if c["command"] == "(ai)":
                    right = hit is None  # a question: it mustn't run a command
                else:
                    right = bool(hit) and hit.command.name == c["command"] and \
                        all(str(hit.slots.get(k)) == str(v) for k, v in c["slots"].items())
                ok += right
                errs.append(word_error_rate(c["text"], heard))
                rows.append({"said": c["text"], "heard": heard, "raw": c["by"][name]["heard"], "voice": c["voice"],
                             "cond": c["cond"], "command": c["command"], "slots": c["slots"],
                             "got": hit.command.name if hit else None, "got_slots": hit.slots if hit else {}, "ok": right})
            n = max(1, len(clips))
            res[name] = {"commands_right": ok, "clips": len(clips), "accuracy": round(100 * ok / n, 1),
                         "word_accuracy": round(100 * (1 - sum(errs) / n), 1), "seconds_per_clip": round(secs / n, 3),
                         "rows": rows}
            self.show(f"  {label} {name}: commands {ok}/{len(clips)} ({res[name]['accuracy']}%), "
                      f"words {res[name]['word_accuracy']}%")
        return res

    # ---------- learning ----------
    def learn(self, scored: dict, min_count: int = 2, dry: bool = False) -> dict:
        from .hearing_plus import AccentFixes
        from .matcher import expand, normalize
        from .understanding import Aliases

        vocab = {w for c in expand(self.config.commands, self.config.slots, variants=True, limit=40) for w in c.words}
        swaps: dict[tuple[str, str], set] = defaultdict(set)
        sentences: dict[tuple[str, str, str], set] = defaultdict(set)
        clashes: Counter = Counter()
        for name, r in scored.items():
            for row in r["rows"]:
                if row["command"] == "(ai)":
                    continue
                # a word it keeps getting wrong is worth fixing even when the fuzzy matching saved the command
                for x, y in AccentFixes.differences(row["raw"], row["said"]):
                    swaps[(x, y)].add(row["voice"])
                if row["ok"]:
                    continue
                heard = normalize(row["heard"])
                if row["got"] is None and len(heard.split()) >= 2:
                    sentences[(heard, row["command"], json.dumps(row["slots"], sort_keys=True))].add(row["voice"])
                elif row["got"] and row["got"] != row["command"]:
                    clashes[(row["said"], row["heard"], row["got"])] += 1
        word_fixes = {}
        for (x, y), voices in swaps.items():
            # often enough, by different voices, and never a word your commands need as it is
            if len(voices) >= min_count and x not in vocab and y in vocab and len(x) > 1:
                word_fixes[x] = y
        fx = AccentFixes(self.config)
        counts = fx.counts()
        conflicts = {}
        for x, y in word_fixes.items():
            others = [k for k in counts if k.startswith(f"{x}>") and not k.endswith(f">{y}")]
            if others:
                conflicts[x] = y  # taught something else before: leave it to the History page
        word_fixes = {x: y for x, y in word_fixes.items() if x not in conflicts}
        m = self.matcher()
        aliases = []
        for (heard, cmd, slots), voices in sentences.items():
            if m.match(heard) is not None:
                continue  # it'd already run something
            aliases.append({"heard": heard, "command": cmd, "slots": json.loads(slots), "voices": len(voices)})
        if not dry:
            for x, y in word_fixes.items():
                counts[f"{x}>{y}"] = max(counts.get(f"{x}>{y}", 0), fx.MIN_SEEN)
            if word_fixes:
                fx.path.parent.mkdir(parents=True, exist_ok=True)
                fx.path.write_text(json.dumps(counts, indent=1), encoding="utf-8")
            al = Aliases(self.config)
            for a in aliases:
                try:
                    al.add(a["heard"], a["command"], a["slots"])
                except Exception as e:
                    log.info("couldn't learn %r: %s", a["heard"], e)
        return {"word_fixes": word_fixes, "sentences": aliases, "clashes": [list(k) + [n] for k, n in clashes.most_common(20)],
                "skipped": conflicts}


def missing_words(config, model_path: Path | None = None) -> list[str]:
    """Words in your commands that Vosk's model has never heard of (it can't recognise them)."""
    path = Path(model_path or config.resolve_path(config.settings.model_path))
    if not (path / "am").exists() and not (path / "graph").exists():
        return []
    try:
        import vosk

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(path))
    except Exception:
        return []
    from .matcher import expand

    words = sorted({w for c in expand(config.commands, config.slots, variants=True, limit=40) for w in c.words})
    find = getattr(model, "find_word", None) or getattr(model, "vosk_model_find_word")
    return [w for w in words if find(w) < 0]


def background(config, files: list[Path], hear: dict[str, Callable[[bytes], str]], show=print) -> dict:
    """Long real audio (music, TV, people talking): cut into sentences, heard, matched. Anything that would
    have run a command is listed: it shouldn't happen."""
    from .hearing import Vad
    from .matcher import Matcher, normalize
    from .understanding import candidates_with_aliases

    s = config.settings
    m = Matcher(candidates_with_aliases(config), s.match_mode, s.match_threshold, s.block_words,
                phonetic=bool(getattr(s, "phonetic_match", True)))
    out = {}
    for name, fn in hear.items():
        fired, pieces, secs = [], 0, 0.0
        for f in files:
            pcm = to_pcm(f)
            secs += len(pcm) / 2 / RATE
            vad = Vad(end_silence=0.6)
            chunks = []
            for i in range(0, len(pcm), 960):
                chunks += [c for c in vad.feed(pcm[i:i + 960]) if c]
            if not chunks:  # no pauses (music): fixed 4-second windows
                chunks = [pcm[i:i + RATE * 8] for i in range(0, len(pcm), RATE * 4)]
            for ch in chunks:
                pieces += 1
                heard = normalize(fn(ch))
                hit = m.match(heard) if heard else None
                if hit:
                    fired.append({"file": Path(f).name, "heard": heard, "command": hit.command.name, "score": round(hit.score)})
        out[name] = {"seconds": round(secs), "pieces": pieces, "fired": fired}
        show(f"  {name}: {round(secs)} s of real audio in {pieces} pieces: {len(fired)} commands would have fired")
        for x in fired[:10]:
            show(f"    {x['file']}: heard \"{x['heard']}\" -> {x['command']} ({x['score']})")
    return out


def report_md(before: dict, after: dict, learned: dict, missing: list[str], bg: dict | None, voices: list[str]) -> str:
    lines = ["# Hearing training report", "", f"Voices: {len(voices)} ({', '.join(voices[:12])}{'...' if len(voices) > 12 else ''})", ""]
    lines += ["| Recognizer | Commands right before | After learning | Words right before | After |", "|---|---|---|---|---|"]
    for name in before:
        b, a = before[name], after.get(name, before[name])
        lines.append(f"| {name} | {b['commands_right']}/{b['clips']} ({b['accuracy']}%) | {a['commands_right']}/{a['clips']} "
                     f"({a['accuracy']}%) | {b['word_accuracy']}% | {a['word_accuracy']}% |")
    lines += ["", "## Learned word fixes", ""] + ([f"- heard \"{x}\" means \"{y}\"" for x, y in learned["word_fixes"].items()] or ["- none"])
    lines += ["", "## Learned wordings", ""] + ([f"- \"{a['heard']}\" means {a['command']} {a['slots'] or ''}" for a in learned["sentences"][:60]] or ["- none"])
    lines += ["", "## Clashes (came out as a different command: consider renaming one)", ""]
    lines += [f"- said \"{s}\", heard \"{h}\", ran {g} ({n}x)" for s, h, g, n in learned["clashes"]] or ["- none"]
    lines += ["", "## Words the Vosk model doesn't know (it can never hear them; Whisper can)", "",
              ", ".join(missing) if missing else "none"]
    for name, r in before.items():
        per = defaultdict(lambda: [0, 0])
        for row in after.get(name, r)["rows"]:
            per[row["voice"]][0] += row["ok"]
            per[row["voice"]][1] += 1
        lines += ["", f"## Per voice ({name}, after)", ""] + [f"- {v}: {ok}/{n}" for v, (ok, n) in sorted(per.items())]
        bad = [row for row in after.get(name, r)["rows"] if not row["ok"]][:40]
        lines += ["", f"## Still wrong ({name})", ""] + [f"- said \"{x['said']}\" ({x['voice']}, {x['cond']}): heard \"{x['heard']}\" -> "
                                                       f"{x['got'] or 'nothing'}" for x in bad]
    if bg:
        lines += ["", "## Real background audio (should run nothing)", ""]
        for name, r in bg.items():
            lines.append(f"- {name}: {r['seconds']} s, {r['pieces']} pieces, {len(r['fired'])} would have fired")
            lines += [f"  - {x['file']}: \"{x['heard']}\" -> {x['command']}" for x in r["fired"][:20]]
    return "\n".join(lines) + "\n"


def run(config, n: int = 300, per_phrase: int = 2, voice_dirs: list[Path] = (), speakers: int = 6,
        noise_file: Path | None = None, background_files: list[Path] = (), dry: bool = False, keep: bool = False,
        hear: dict | None = None, voices: list[Voice] | None = None, show=print, seed: int = 1,
        items: list[dict] | None = None) -> dict:
    from .speechtest import recognizers

    rng = random.Random(seed)
    voices = voices if voices is not None else find_voices(voice_dirs, speakers, rng)
    if not voices:
        raise SystemExit("No voices to train with: on a Mac the built-in ones are used; elsewhere give --voices "
                         "(a folder of Piper .onnx voices) or install espeak-ng")
    hear = hear if hear is not None else recognizers(config)
    if not hear:
        raise SystemExit("No speech recognizer: run phrasehome download-models (Vosk) or pip install faster-whisper")
    noise = np.frombuffer(to_pcm(noise_file), dtype=np.int16) if noise_file else None
    items = items if items is not None else phrases(config, n, seed)
    show(f"Training on {len(items)} phrases x {per_phrase} voices, from {len(voices)} voices, through mp3, "
         f"with {', '.join(hear)}")
    t = Trainer(config, voices, hear, noise, seed=seed, keep=keep, show=show)
    clips = t.make_clips(items, per_phrase)
    before = t.score(clips, "before")
    learned = t.learn(before, dry=dry)
    show(f"  learned {len(learned['word_fixes'])} word fixes and {len(learned['sentences'])} wordings"
         + (" (dry run: not saved)" if dry else ""))
    after = before if dry else t.score(clips, "after ")
    missing = missing_words(config)
    bg = background(config, list(background_files), hear, show) if background_files else None
    t.out.mkdir(parents=True, exist_ok=True)
    md = report_md(before, after, learned, missing, bg, [v.name for v in voices])
    (t.out / "report.md").write_text(md)
    (t.out / "report.json").write_text(json.dumps({"before": {k: {x: y for x, y in v.items() if x != "rows"} for k, v in before.items()},
                                                   "after": {k: {x: y for x, y in v.items() if x != "rows"} for k, v in after.items()},
                                                   "learned": learned, "missing_words": missing,
                                                   "background": bg}, indent=1, default=str))
    show(f"Report: {t.out / 'report.md'}")
    return {"before": before, "after": after, "learned": learned, "missing": missing, "background": bg,
            "voices": [v.name for v in voices], "clips": clips}


def main(args) -> int:
    from .config import load_config

    cfg = load_config(args.config)
    run(cfg, n=args.phrases, per_phrase=args.per_phrase, voice_dirs=[Path(v) for v in args.voices or []],
        speakers=args.speakers, noise_file=Path(args.noise) if args.noise else None,
        background_files=[Path(b) for b in args.background or []], dry=args.dry, keep=args.keep)
    return 0


_SHIPPED: dict | None = None


def shipped() -> dict:
    """What train-hearing learned about the built-in commands before release (trained_hearing.json)."""
    global _SHIPPED
    if _SHIPPED is None:
        try:
            _SHIPPED = json.loads((Path(__file__).with_name("trained_hearing.json")).read_text())
        except (OSError, ValueError):
            _SHIPPED = {}
    return _SHIPPED
