"""Measure how well the house hears you, so changes can be judged by numbers, not feel.

    phrasehome speech-test --record     say 15 phrases from your commands (saved in data/speech-test/)
    phrasehome speech-test --tts        or let the house's own voice say them (quick, less realistic)
    phrasehome speech-test              score the saved clips with Vosk and Whisper, as set up now

Everyone in the family (family_test.py):
    phrasehome family-test --record --person sam -n 50     Sam says 50 of your commands
    phrasehome family-test                                 score everyone; what each voice gets wrong

Prints, for each recognizer: word accuracy (100% = every word right), how many clips ran the right
command, and the time per sentence. Saved clips stay, so run it again after any change.
"""

from __future__ import annotations

import json
import random
import time
import wave
from pathlib import Path
from typing import Callable

RATE = 16000


def folder(config, person: str | None = None) -> Path:
    base = Path(config.resolve_path(config.data_dir)) / "speech-test"
    return base / "people" / person.lower() if person else base


def word_error_rate(expected: str, heard: str) -> float:
    """Word-level edit distance / number of expected words (0 = perfect)."""
    from .matcher import normalize

    ref, hyp = normalize(expected).split(), normalize(heard).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
            prev, d[j] = d[j], cur
    return d[len(hyp)] / len(ref)


def choose_phrases(config, n: int = 15, seed: int | None = None) -> list[dict]:
    """A mix of your commands (with slots filled in) and a couple of AI questions."""
    from .matcher import expand

    rng = random.Random(seed)
    by_cmd: dict[str, list] = {}
    for c in expand(config.commands, config.slots):
        by_cmd.setdefault(c.command.name, []).append(c)
    picks = []
    names = list(by_cmd)
    rng.shuffle(names)
    for name in names[: max(1, n - 2)]:
        cand = rng.choice(by_cmd[name])
        picks.append({"expected": cand.text, "command": name})
    wake = (config.assistant.get("names") or ["house"])[0] if config.assistant else None
    if wake:
        picks += [{"expected": f"{wake} what's the weather tomorrow", "command": "(ai)"},
                  {"expected": f"{wake} tell me a story about the kitchen", "command": "(ai)"}]
    return picks[:n]


def _write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def _read_wav(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        return w.readframes(w.getnframes())


def save_set(config, items: list[dict], get_audio: Callable[[dict, int], bytes], person: str | None = None) -> Path:
    out = folder(config, person)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for i, item in enumerate(items, 1):
        pcm = get_audio(item, i)
        if not pcm:
            continue
        f = out / f"{i:02d}.wav"
        _write_wav(f, pcm)
        manifest.append({**item, "file": f.name})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return out


def record(config, items: list[dict], seconds: float = 3.5, show=print, person: str | None = None) -> Path:
    import sounddevice as sd

    def get(item, i):
        show(f"\n[{i}/{len(items)}] Say: \"{item['expected']}\"  (recording in 1 s, for {seconds:.0f} s)")
        time.sleep(1.0)
        data = sd.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="int16")
        sd.wait()
        return data.tobytes()

    return save_set(config, items, get, person)


def synthesize(config, items: list[dict], show=print) -> Path:
    from .audio_out import convert
    from .voice import Voice

    v = Voice(config)

    def get(item, i):
        show(f"[{i}/{len(items)}] {item['expected']}")
        wav = v.synthesize(item["expected"], v.base)
        return convert(wav, RATE, 1) if wav else b""

    return save_set(config, items, get)


def recognizers(config) -> dict[str, Callable[[bytes], str]]:
    """The ways this computer can hear: Vosk as set up, and Whisper if it's installed."""
    out: dict[str, Callable[[bytes], str]] = {}
    s = config.settings
    from .hearing import StreamCleaner, clean

    try:
        import vosk

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(config.resolve_path(s.model_path)))
        from .listener import _Lang

        names = list(config.assistant.get("names") or []) if config.assistant else []
        lang = _Lang(s.language, model, config, [w for n in names for w in n.lower().split()])

        def run_vosk(pcm: bytes) -> str:
            rec = vosk.KaldiRecognizer(model, RATE, lang.grammar) if lang.grammar else vosk.KaldiRecognizer(model, RATE)
            sc = StreamCleaner() if s.noise_cleanup else None
            for i in range(0, len(pcm), 8000):
                chunk = pcm[i:i + 8000]
                rec.AcceptWaveform(sc(chunk) if sc else chunk)
            return json.loads(rec.FinalResult()).get("text", "")

        out["vosk" + (" (your words only)" if lang.grammar else "")] = run_vosk
    except Exception as e:
        print(f"(Vosk not available: {e})")
    try:
        from .hearing import Whisper, house_words

        w = Whisper(s.whisper_model)
        prompt = house_words(config, list(config.assistant.get("names") or []) if config.assistant else [])

        def run_whisper(pcm: bytes) -> str:
            return w.transcribe(clean(pcm) if s.noise_cleanup else pcm, prompt, s.language)[0]

        out[f"whisper {w.name}"] = run_whisper
    except Exception as e:
        print(f"(Whisper not available: {e})")
    return out


def score(config, hear: dict[str, Callable[[bytes], str]] | None = None, show=print,
          person: str | None = None) -> dict[str, dict]:
    from .matcher import Matcher
    from .understanding import candidates_with_aliases

    out = folder(config, person)
    try:
        items = json.loads((out / "manifest.json").read_text())
    except (OSError, ValueError):
        raise SystemExit("No saved clips yet. Run: phrasehome speech-test --record (or --tts)")
    hear = hear if hear is not None else recognizers(config)
    s = config.settings
    matcher = Matcher(candidates_with_aliases(config), s.match_mode, s.match_threshold, s.block_words)
    results: dict[str, dict] = {}
    for name, fn in hear.items():
        rows, errs, right, secs = [], [], 0, 0.0
        for item in items:
            pcm = _read_wav(out / item["file"])
            t0 = time.monotonic()
            text = fn(pcm)
            secs += time.monotonic() - t0
            err = word_error_rate(item["expected"], text)
            m = matcher.match(text)
            got = m.command.name if m else ("(ai)" if item["command"] == "(ai)" and text else None)
            ok = got == item["command"]
            right += ok
            errs.append(err)
            rows.append({"expected": item["expected"], "heard": text, "command": item["command"], "matched": got,
                         "ok": ok, "word_error": round(err, 2)})
        n = max(len(items), 1)
        results[name] = {"word_accuracy": round(100 * (1 - sum(errs) / n)), "commands_right": right,
                         "clips": len(items), "seconds_per_clip": round(secs / n, 2), "rows": rows}
        show(f"\n{name}: words {results[name]['word_accuracy']}% right, commands {right}/{len(items)}, "
             f"{results[name]['seconds_per_clip']} s per sentence")
        for r in rows:
            if not r["ok"] or r["word_error"] > 0:
                show(f"  said \"{r['expected']}\"  heard \"{r['heard']}\"" + ("" if r["ok"] else
                     f"  -> {r['matched'] or 'nothing'} (wanted {r['command']})"))
    (out / "last-score.json").write_text(json.dumps(results, indent=1))
    return results
