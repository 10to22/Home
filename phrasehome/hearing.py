"""Better hearing: cut sentences out of the audio, clean them up, and write them down with Whisper.

settings:
  recognizer: whisper        # instead of vosk (pip install faster-whisper; the model downloads itself)
  whisper_model: small.en    # tiny.en / base.en (faster) ... small.en / medium.en (more accurate)
  noise_cleanup: true
  vad_silence: 0.6           # seconds of quiet that end a sentence

Pieces:
  Vad           finds where each sentence starts and ends (keeps a moment before the start, so
                first words aren't clipped); uses webrtcvad too if it's installed
  clean()       removes low rumble, turns down steady background noise, evens out the volume
  StreamCleaner the same rumble filter + volume levelling, for live audio (the Vosk recognizer)
  Whisper       writes a sentence down, primed with the house's own words (names, rooms,
                commands) so it picks "Finn" and "freak mode" over sound-alikes
  WakeSpotter   a tiny recognizer that only knows the AI's name ("house"), to catch it even when
                the full transcript mishears it
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from typing import Any

import numpy as np

log = logging.getLogger("phrasehome.hearing")

RATE = 16000
FRAME = 480  # 30 ms at 16 kHz
HALLUCINATIONS = {"thank you", "thanks for watching", "you", "bye", "thank you for watching", "so", "okay",
                  "uh", "um", "hmm", "the end"}  # what Whisper "hears" in silence


def _db(frame: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(frame.astype(np.float64) ** 2))) / 32768.0
    return 20 * math.log10(rms + 1e-9)


class Vad:
    """Feed live audio; get back whole sentences (16-bit mono PCM) as they finish."""

    def __init__(self, end_silence: float = 0.6, preroll: float = 0.3, max_seconds: float = 15.0,
                 min_speech: float = 0.25) -> None:
        self.end_frames = max(3, int(end_silence / 0.03))
        self.pre_frames = int(preroll / 0.03)
        self.max_frames = int(max_seconds / 0.03)
        self.min_speech = int(min_speech / 0.03)
        self.noise_db = -60.0
        self._buf = b""
        self._pre: list[bytes] = []
        self._cur: list[bytes] | None = None
        self._recent: list[bool] = []
        self._quiet = 0
        self._speech = 0
        try:
            import webrtcvad

            self._web = webrtcvad.Vad(2)
        except Exception:
            self._web = None

    @property
    def active(self) -> bool:
        """In the middle of a sentence."""
        return self._cur is not None

    def _is_speech(self, frame: bytes, db: float) -> bool:
        loud = db > max(self.noise_db + 10, -55)
        if self._web is not None:
            try:
                return loud and self._web.is_speech(frame, RATE)
            except Exception:
                pass
        return loud

    def feed(self, pcm: bytes) -> list[bytes]:
        done: list[bytes] = []
        self._buf += pcm
        while len(self._buf) >= FRAME * 2:
            frame, self._buf = self._buf[: FRAME * 2], self._buf[FRAME * 2:]
            db = _db(np.frombuffer(frame, dtype=np.int16))
            speech = self._is_speech(frame, db)
            if not speech and self._cur is None:  # follow the room's background level
                rate = 0.2 if db < self.noise_db else 0.02
                self.noise_db += (db - self.noise_db) * rate
            self._recent = (self._recent + [speech])[-5:]
            if self._cur is None:
                self._pre = (self._pre + [frame])[-self.pre_frames:] if self.pre_frames else []
                if sum(self._recent) >= 3:  # 3 of the last 5 frames: someone's talking
                    self._cur, self._pre, self._quiet, self._speech = list(self._pre), [], 0, 0
                continue
            self._cur.append(frame)
            self._speech += speech
            self._quiet = 0 if speech else self._quiet + 1
            if self._quiet >= self.end_frames or len(self._cur) >= self.max_frames:
                if self._speech >= self.min_speech:
                    done.append(b"".join(self._cur))
                self._cur, self._recent, self._quiet = None, [], 0
        return done


# ---------- noise cleanup ----------
def _highpass(x: np.ndarray, cutoff: float = 100.0, state: tuple[float, float] = (0.0, 0.0)):
    """First-order high-pass (removes rumble, desk bumps, mains hum's lowest part)."""
    rc = 1.0 / (2 * math.pi * cutoff)
    a = rc / (rc + 1.0 / RATE)
    y = np.empty_like(x)
    prev_x, prev_y = state
    for i in range(len(x)):  # short clips: plain loop is fine, and keeps the state exact
        prev_y = a * (prev_y + x[i] - prev_x)
        prev_x = x[i]
        y[i] = prev_y
    return y, (prev_x, prev_y)


def _highpass_fast(x: np.ndarray, cutoff: float = 100.0) -> np.ndarray:
    """The same filter for a whole clip, without a Python loop (via the FFT)."""
    n = len(x)
    if n == 0:
        return x
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / RATE)
    spec *= f / np.sqrt(f ** 2 + cutoff ** 2)  # first-order high-pass magnitude
    return np.fft.irfft(spec, n)


def _denoise(x: np.ndarray, n_fft: int = 512, hop: int = 256, floor: float = 0.2) -> np.ndarray:
    """Spectral gating: learn the steady background from the quietest moments, then turn
    each frequency down where it's no louder than that background."""
    if len(x) < n_fft * 4:
        return x
    win = np.hanning(n_fft)
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(x, (n_fft, n_fft)), n_fft)[::hop] * win
    spec = np.fft.rfft(frames, axis=1)
    mag = np.abs(spec)
    energy = mag.sum(axis=1)
    quiet = mag[energy <= np.percentile(energy, 20)]
    noise = np.median(quiet, axis=0) if len(quiet) else np.zeros(mag.shape[1])
    gain = np.clip(1.0 - 1.5 * noise / (mag + 1e-9), floor, 1.0)
    gain = (gain + np.vstack([gain[:1], gain[:-1]]) + np.vstack([gain[1:], gain[-1:]])) / 3  # smooth in time
    out_frames = np.fft.irfft(spec * gain, n_fft, axis=1) * win
    out = np.zeros(len(x) + 2 * n_fft + n_fft)
    norm = np.zeros_like(out)
    for i, f in enumerate(out_frames):
        out[i * hop: i * hop + n_fft] += f
        norm[i * hop: i * hop + n_fft] += win ** 2
    out = out / np.maximum(norm, 1e-6)
    return out[n_fft: n_fft + len(x)]


def _level(x: np.ndarray, target_db: float = -20.0, max_gain: float = 20.0) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2))) + 1e-9
    gain = min(max_gain, 10 ** (target_db / 20) / rms)
    y = x * gain
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    return y * (0.95 / peak) if peak > 0.95 else y


def clean(pcm: bytes) -> bytes:
    """A whole sentence: rumble out, background down, volume evened out."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
    if not len(x):
        return pcm
    x = _level(_denoise(_highpass_fast(x)))
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


class StreamCleaner:
    """Rumble filter + slow volume levelling for live audio (cheap enough for every chunk)."""

    def __init__(self, target_db: float = -24.0, max_gain: float = 8.0) -> None:
        self.state = (0.0, 0.0)
        self.gain = 1.0
        self.target = 10 ** (target_db / 20)
        self.max_gain = max_gain

    def __call__(self, pcm: bytes) -> bytes:
        x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
        if not len(x):
            return pcm
        x, self.state = _highpass(x, state=self.state)
        rms = float(np.sqrt(np.mean(x ** 2))) + 1e-9
        if rms > 0.003:  # only level when there's something to hear, not silence
            want = min(self.max_gain, self.target / rms)
            self.gain += (want - self.gain) * 0.1
        y = np.clip(x * self.gain, -1, 1)
        return (y * 32767).astype(np.int16).tobytes()


# ---------- Whisper ----------
def default_whisper_model() -> str:
    """A Mac with 16 GB or more gets distil-medium.en (much more accurate, still quick on Apple silicon)."""
    if sys.platform == "darwin":
        from .speech_tuning import memory_gb

        return "distil-medium.en" if memory_gb() >= 15 else "small.en"
    return "base.en"


def house_words(config, extra_names: list[str] | None = None, limit: int = 700) -> str:
    """The words this house uses (names, rooms, commands), to prime Whisper with."""
    words: list[str] = []

    def add(w: Any) -> None:
        w = str(w).strip()
        if w and w.lower() not in {x.lower() for x in words}:
            words.append(w)

    for n in extra_names or []:
        add(n)
    try:  # what it has learned to expect (accent fixes, taught sentences)
        from .speech_tuning import learned_words

        for w in learned_words(config):
            add(w)
    except Exception:
        pass
    for inst in config.instances:
        add(inst.name)
        for r in inst.rooms:
            add(r)
    for vals in config.slots.values():
        for v in list(vals)[:30]:
            if not str(v).isdigit():
                add(v)
    for cmd in config.commands:
        if cmd.phrases and "{" not in cmd.phrases[0]:
            add(cmd.phrases[0])
    out = ""
    for w in words:
        if len(out) + len(w) + 2 > limit:
            break
        out = f"{out}, {w}" if out else w
    return out + "." if out else ""


class Whisper:
    """faster-whisper on the CPU (works on a Mac and a Pi). The model downloads on first use."""

    def __init__(self, model: str = "", compute_type: str = "int8", beam: int = 1) -> None:
        self.beam = max(1, int(beam))
        self.hotwords = ""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError("Whisper isn't installed: .venv/bin/pip install faster-whisper") from None
        self.name = model or default_whisper_model()
        log.info("loading Whisper %s (downloads once, the first time)", self.name)
        self.model = WhisperModel(self.name, device="cpu", compute_type=compute_type)

    def transcribe(self, pcm: bytes, prompt: str = "", language: str = "en") -> tuple[str, float, list[dict]]:
        """(text, confidence 0-1, [{word, conf}])"""
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        kw = dict(language=(language or "en")[:2], beam_size=getattr(self, "beam", 1), initial_prompt=prompt or None,
                  word_timestamps=True, condition_on_previous_text=False, vad_filter=False)
        if getattr(self, "hotwords", ""):
            kw["hotwords"] = self.hotwords  # names and learned words it should expect
        try:
            segments, _ = self.model.transcribe(audio, **kw)
        except TypeError:  # an older faster-whisper without hotwords
            kw.pop("hotwords", None)
            segments, _ = self.model.transcribe(audio, **kw)
        segments = [s for s in segments if getattr(s, "no_speech_prob", 0) < 0.6]
        words = [{"word": w.word.strip(), "conf": round(float(w.probability), 3)}
                 for s in segments for w in (getattr(s, "words", None) or []) if w.word.strip()]
        text = " ".join(s.text.strip() for s in segments).strip()
        if words:
            conf = sum(w["conf"] for w in words) / len(words)
        elif segments:
            conf = sum(math.exp(s.avg_logprob) for s in segments) / len(segments)
        else:
            conf = 0.0
        if re.sub(r"[^a-z ]", "", text.lower()).strip() in HALLUCINATIONS and conf < 0.7:
            return "", 0.0, []
        return text, conf, words


# ---------- the AI's name ----------
class WakeSpotter:
    """A recognizer that only knows the wake names (and "something else"). It's very good at one
    thing: noticing "house" at the start of a sentence, even when the full transcript says "how's"."""

    def __init__(self, vosk_module, model, names: list[str], rate: int = RATE) -> None:
        words = sorted({w for n in names for w in n.lower().split()})
        self.names = [n.lower() for n in names]
        self.rec = vosk_module.KaldiRecognizer(model, rate, json.dumps(words + ["[unk]"]))
        self.rec.SetWords(True)
        self.last = 0.0

    def feed(self, pcm: bytes, now: float) -> bool:
        if not self.rec.AcceptWaveform(pcm):
            return False
        result = json.loads(self.rec.Result())
        heard = [w for w in result.get("result") or [] if w.get("word") != "[unk]"]
        if heard and heard[0].get("conf", 0) >= 0.8 and any(heard[0]["word"] == n.split()[0] for n in self.names):
            self.last = now
            return True
        return False

    def heard_recently(self, now: float, window: float = 20.0) -> bool:
        return now - self.last < window


class OpenWakeWordSpotter:
    """A real wake word engine (openWakeWord, pip install openwakeword): a small neural model trained on
    one phrase, much better than a speech recognizer at hearing "hey home" across a room or over music.

    settings:
      wake_engine: openwakeword
      wake_models: [models/hey_home.onnx]   # train your own phrase with openWakeWord's training notebook;
                                           # built-in ones work too: hey_jarvis, hey_mycroft, alexa
      wake_threshold: 0.5

    Same interface as WakeSpotter: feed() audio, then heard_recently()."""

    FRAME = 1280  # 80 ms at 16 kHz, what openWakeWord expects

    def __init__(self, models: list[str] | None = None, threshold: float = 0.5, rate: int = RATE,
                 model_class=None) -> None:
        if rate != 16000:
            raise RuntimeError("openWakeWord needs 16 kHz audio (settings: sample_rate: 16000)")
        if model_class is None:
            try:
                from openwakeword.model import Model as model_class
            except ImportError:
                raise RuntimeError("openWakeWord isn't installed: .venv/bin/pip install openwakeword") from None
        models = [str(m) for m in (models or [])]
        framework = "tflite" if models and all(m.endswith(".tflite") for m in models) else "onnx"
        kw = {"wakeword_models": models} if models else {}
        try:
            self.model = model_class(inference_framework=framework, **kw)
        except TypeError:  # older openWakeWord
            self.model = model_class(**kw)
        self.threshold = float(threshold)
        self.buf = np.zeros(0, dtype=np.int16)
        self.last = 0.0
        self.best = 0.0  # highest score in the last feed, for tuning

    def feed(self, pcm: bytes, now: float) -> bool:
        self.buf = np.concatenate([self.buf, np.frombuffer(pcm, dtype=np.int16)])
        hit = False
        self.best = 0.0
        while len(self.buf) >= self.FRAME:
            frame, self.buf = self.buf[: self.FRAME], self.buf[self.FRAME:]
            scores = self.model.predict(frame) or {}
            top = max((float(v) for v in scores.values()), default=0.0)
            self.best = max(self.best, top)
            if top >= self.threshold:
                hit = True
        if hit:
            self.last = now
            log.debug("wake word (%.2f)", self.best)
        return hit

    def heard_recently(self, now: float, window: float = 20.0) -> bool:
        return now - self.last < window


def sounds_like_name(word: str, names: list[str]) -> bool:
    """Did the transcript mishear the AI's name ("how's", "mouse" for "house")?"""
    from rapidfuzz import fuzz

    w = re.sub(r"[^a-z]", "", word.lower())
    return bool(w) and any(fuzz.ratio(w, n.split()[0]) >= 60 for n in names)
