"""Find the installed voice that sounds most like a recording.

    phrasehome voice match some_recording.mp3

Measures a few things about how a voice sounds: how high it is (median pitch), how flat or
lively it is (pitch spread), how bright or dark it is (spectral centroid) and how fast it talks
(syllable-like energy peaks per second). Then it makes a test sentence with every voice this
computer has (macOS `say` voices, Piper models in models/piper, espeak-ng), with and without the
voice effects, measures them the same way, and ranks them by how close they are.

It only picks among voices you already have; it doesn't copy or clone the recording.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("phrasehome.voice_match")

RATE = 16000
TEST_TEXT = ("The kitchen lights are on, the front door is locked, and the laundry finished "
             "twenty minutes ago. I would mention that it is still in the machine, but you "
             "probably already knew that.")


@dataclass
class Profile:
    pitch_hz: float  # median pitch of voiced speech
    spread_st: float  # pitch spread in semitones: low = flat and monotone
    brightness_hz: float  # median spectral centroid
    pace: float  # syllable-like energy peaks per second

    def describe(self) -> str:
        flat = "very flat" if self.spread_st < 1.5 else "flat" if self.spread_st < 2.5 else \
            "lively" if self.spread_st < 4.5 else "very lively"
        return (f"pitch {self.pitch_hz:.0f} Hz, {flat} ({self.spread_st:.1f} semitones), "
                f"brightness {self.brightness_hz:.0f} Hz, pace {self.pace:.1f}/s")


def load(path: Path) -> np.ndarray:
    """Any audio file -> 16 kHz mono float (needs ffmpeg)."""
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                          "-ar", str(RATE), "-"], capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _frames(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    if len(x) < size:
        x = np.pad(x, (0, size - len(x)))
    n = 1 + (len(x) - size) // hop
    idx = np.arange(size)[None, :] + hop * np.arange(n)[:, None]
    return x[idx]


def pitch_track(x: np.ndarray, fmin: float = 65, fmax: float = 500) -> tuple[np.ndarray, np.ndarray]:
    """YIN pitch per 10 ms frame. Returns (pitch Hz or 0 when unvoiced, frame energy)."""
    size, hop = 1024, RATE // 100
    fr = _frames(x, size, hop)
    fr = fr - fr.mean(axis=1, keepdims=True)
    energy = np.sqrt((fr ** 2).mean(axis=1))
    lo, hi = int(RATE / fmax), int(RATE / fmin)
    w = size // 2
    # difference function d(tau) = sum (x[j] - x[j+tau])^2 over a window of w samples, via FFT
    spec = np.fft.rfft(fr, n=2 * size)
    ac = np.fft.irfft(spec * np.conj(np.fft.rfft(fr[:, :w], n=2 * size)), n=2 * size)[:, :hi + 1]
    sq = np.cumsum(np.pad(fr ** 2, ((0, 0), (1, 0))), axis=1)
    e0 = sq[:, w:w + 1] - sq[:, 0:1]
    taus = np.arange(hi + 1)
    et = sq[:, taus + w] - sq[:, taus]
    d = e0 + et - 2 * ac
    d[:, 0] = 0
    cum = np.cumsum(d[:, 1:], axis=1) / np.arange(1, hi + 1)
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] / np.maximum(cum, 1e-12)
    seg = cmnd[:, lo:hi + 1]
    best = seg.argmin(axis=1)
    below = seg < 0.15
    first = np.where(below.any(axis=1), below.argmax(axis=1), best)
    # walk to the local minimum after the first dip under the threshold
    tau = first.copy()
    for i in range(len(tau)):
        t = tau[i]
        while t + 1 < seg.shape[1] and seg[i, t + 1] < seg[i, t]:
            t += 1
        tau[i] = t
    conf = seg[np.arange(len(tau)), tau]
    pitch = RATE / (tau + lo)
    loud = energy > max(0.02, 0.1 * np.percentile(energy, 95))
    voiced = (conf < 0.25) & loud
    return np.where(voiced, pitch, 0.0), energy


def profile(x: np.ndarray) -> Profile:
    pitch, energy = pitch_track(x)
    f0 = pitch[pitch > 0]
    if len(f0) < 20:
        raise ValueError("couldn't hear enough speech in that recording")
    st = 12 * np.log2(f0 / np.median(f0))
    lo, hi = np.percentile(st, [10, 90])
    spread = float(np.std(st[(st >= lo) & (st <= hi)]))
    # brightness: spectral centroid of the louder frames
    fr = _frames(x, 512, RATE // 100) * np.hanning(512)
    mag = np.abs(np.fft.rfft(fr, axis=1))
    freqs = np.fft.rfftfreq(512, 1 / RATE)
    e = mag.sum(axis=1)
    keep = e > np.percentile(e, 50)
    centroid = float(np.median((mag[keep] * freqs).sum(axis=1) / np.maximum(e[keep], 1e-9)))
    # pace: peaks in the smoothed loudness, about one per syllable
    env = np.convolve(energy, np.hanning(9) / np.hanning(9).sum(), mode="same")
    thr = 0.3 * np.percentile(env, 95)
    peaks = (env[1:-1] > env[:-2]) & (env[1:-1] >= env[2:]) & (env[1:-1] > thr)
    talking = max((energy > 0.1 * np.percentile(energy, 95)).sum() / 100, 0.5)
    return Profile(float(np.median(f0)), spread, centroid, float(peaks.sum() / talking))


def distance(a: Profile, b: Profile) -> float:
    """How different two voices sound (0 = the same on everything measured)."""
    pitch = abs(12 * np.log2(a.pitch_hz / b.pitch_hz)) / 2.0  # 2 semitones = 1 unit
    spread = abs(a.spread_st - b.spread_st) / 1.0
    bright = abs(np.log2(a.brightness_hz / b.brightness_hz)) / 0.25
    pace = abs(np.log2(a.pace / b.pace)) / 0.3
    return float(np.sqrt(1.5 * pitch ** 2 + 1.5 * spread ** 2 + bright ** 2 + 0.5 * pace ** 2))


# ---------- the voices this computer has ----------
def say_voices() -> list[str]:
    if sys.platform != "darwin" or not shutil.which("say"):
        return []
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=30).stdout
    return parse_say_list(out)


def parse_say_list(text: str) -> list[str]:
    """`say -v '?'` lines look like 'Samantha            en_US    # Hello! My name is Samantha.'
    (newer macOS: 'Eddy (English (US))   en_US    # ...'). English voices only."""
    names = []
    for line in text.splitlines():
        left = line.split("#", 1)[0].rstrip()
        parts = left.rsplit(None, 1)
        if len(parts) == 2 and parts[1].lower().startswith("en"):
            names.append(parts[0].strip())
    return names


def candidates(config) -> list[dict]:
    """Voice settings to try, as they'd go in config.yaml's `voice:`."""
    out = [{"engine": "say", "say_voice": n} for n in say_voices()]
    if shutil.which("piper"):
        folder = Path(config.resolve_path("models/piper"))
        out += [{"engine": "piper", "piper_model": str(p.relative_to(config.base_dir))
                 if p.is_relative_to(config.base_dir) else str(p)}
                for p in sorted(folder.glob("*.onnx"))]
    if shutil.which("espeak-ng"):
        for base in ("en-us", "en"):  # "en" is British; espeak ignores variants on "en-gb"
            out += [{"engine": "espeak", "espeak_voice": f"{base}+{v}"}
                    for v in ("f1", "f2", "f3", "f4", "f5", "m1", "m3")]
    return out


def match(config, reference: Path, top: int = 10, effects: bool = True, progress=print) -> tuple[Profile, list]:
    from .voice import EFFECTS, Voice

    ref = profile(load(reference))
    voice = Voice(config)
    voice.cache = Path(tempfile.mkdtemp(prefix="voicematch-"))
    tries = candidates(config)
    if not tries:
        raise RuntimeError("no voices found to compare (on a Mac this uses the built-in `say` voices)")
    fx = [None] + (list(EFFECTS) if effects and shutil.which("ffmpeg") else [])
    results, heard = [], set()
    for i, base in enumerate(tries, 1):
        label = base.get("say_voice") or Path(base.get("piper_model", "")).stem or base.get("espeak_voice")
        progress(f"  [{i}/{len(tries)}] {label}")
        for effect in fx:
            v = {"speed": 1.0, "volume": 100, **base, "effect": effect}
            try:
                wav = voice.synthesize(TEST_TEXT, v)
                digest = hashlib.sha1(wav.read_bytes()).hexdigest()
                if digest in heard:
                    continue  # the same sound under another name
                heard.add(digest)
                p = profile(load(wav))
            except Exception as e:
                log.debug("skipped %s %s: %s", label, effect, e)
                continue
            results.append((distance(ref, p), label, effect, {k: x for k, x in v.items()
                                                               if k in base or (k == "effect" and x)}, p))
    shutil.rmtree(voice.cache, ignore_errors=True)
    results.sort(key=lambda r: r[0])
    return ref, results[:top]
