"""The bigger voices: MeloTTS (has a real Australian accent), Chatterbox (acts out emotion, and can
speak in a voice from a short sample you recorded), and any voice server on your network.

    voice:
      engine: melo                 # pip install melotts  (CPU is fine; first use downloads its model)
      melo: { speaker: EN-AU, device: auto }

      engine: chatterbox           # pip install chatterbox-tts  (a GPU or Apple Silicon Mac helps a lot)
      chatterbox:
        sample: voice_bank/my_voice.wav   # 10-20 s of one person talking. Your own voice, or someone who
        sample_consent: true              #   said yes. Never a celebrity or anyone who hasn't agreed.
        device: auto

      engine: http                 # a voice server: `phrasehome voice serve` on a Mac or PC, or any
      http:                        # OpenAI-style /v1/audio/speech server (chatterbox-tts-api, Kokoro-FastAPI)
        url: http://studio.local:8810/v1/audio/speech
        voice: default
        token_env: PHRASEHOME_VOICE_TOKEN
        mood_fields: true          # send emotion / exaggeration / cfg_weight too (false for strict servers)

A Raspberry Pi is too slow for Chatterbox: run it on a laptop with `phrasehome voice serve` and point
the Pi at it with `engine: http`. The Pi falls back to its own voice if the server is unreachable.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.tts")

_models: dict[str, Any] = {}
_model_lock = threading.Lock()
_warned: set[str] = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        log.warning(msg, *args)


def _device(want: Any) -> str:
    want = str(want or "auto").lower()
    if want != "auto":
        return want
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


# ---------- MeloTTS ----------
def melo(text: str, out: Path, cfg: dict, params: dict, accent: str | None) -> None:
    try:
        from melo.api import TTS  # type: ignore
    except ImportError as e:
        raise RuntimeError("engine melo needs MeloTTS: pip install git+https://github.com/myshell-ai/MeloTTS "
                           "&& python -m unidic download (first use downloads its model, a few hundred MB)") from e
    from .voice_style import ACCENTS

    device = _device(cfg.get("device"))
    with _model_lock:
        model = _models.get(f"melo|{device}")
        if model is None:
            model = TTS(language=str(cfg.get("language", "EN")), device=device)
            _models[f"melo|{device}"] = model
    speakers = dict(model.hps.data.spk2id)
    want = cfg.get("speaker") or (ACCENTS[accent]["melo"] if accent else "EN-Default")
    spk = speakers.get(want) if want in speakers else next(
        (v for k, v in speakers.items() if k.lower().replace("_", "-") == str(want).lower().replace("_", "-")), None)
    if spk is None:
        _warn_once(f"melo-{want}", "MeloTTS has no speaker %r (have: %s); using the first", want, ", ".join(speakers))
        spk = next(iter(speakers.values()))
    model.tts_to_file(text, spk, str(out), speed=params.get("speed", 1.0), sdp_ratio=params.get("sdp_ratio", 0.2),
                      noise_scale=params.get("noise_scale", 0.6), noise_scale_w=params.get("noise_scale_w", 0.8),
                      quiet=True)


# ---------- Chatterbox ----------
def chatterbox_sample(config, cfg: dict) -> Path | None:
    """The voice sample to speak like, only with the owner's say-so (sample_consent: true)."""
    sample = cfg.get("sample")
    if not sample:
        return None
    path = Path(config.resolve_path(sample))
    if not cfg.get("sample_consent"):
        _warn_once("consent", "voice.chatterbox.sample is set but sample_consent isn't: using Chatterbox's own "
                   "voice. Only use a recording of yourself, or of someone who agreed to it, then set "
                   "sample_consent: true.")
        return None
    if not path.exists():
        _warn_once(f"sample-{path}", "voice sample %s doesn't exist: using Chatterbox's own voice", path)
        return None
    return path


def chatterbox(text: str, out: Path, cfg: dict, params: dict, sample: Path | None) -> None:
    try:
        from chatterbox.tts import ChatterboxTTS  # type: ignore
    except ImportError as e:
        raise RuntimeError("engine chatterbox needs Chatterbox: pip install chatterbox-tts (with PyTorch; first "
                           "use downloads its model, a few GB). On a Pi, run it on a laptop with "
                           "`phrasehome voice serve` and use engine: http") from e
    import numpy as np

    device = _device(cfg.get("device"))
    with _model_lock:
        model = _models.get(f"chatterbox|{device}")
        if model is None:
            model = ChatterboxTTS.from_pretrained(device=device)
            _models[f"chatterbox|{device}"] = model
        kw = {"exaggeration": params.get("exaggeration", 0.5), "cfg_weight": params.get("cfg_weight", 0.5)}
        if sample:
            kw["audio_prompt_path"] = str(sample)
        if cfg.get("temperature") is not None:
            kw["temperature"] = float(cfg["temperature"])
        audio = model.generate(text, **kw)
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    write_wav(out, np.asarray(audio, dtype=np.float32).reshape(-1), int(getattr(model, "sr", 24000)))


def write_wav(path: Path, samples, rate: int) -> None:
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


# ---------- a voice server on the network ----------
def http(text: str, out: Path, cfg: dict, params: dict, timeout: float = 60) -> None:
    url = cfg.get("url")
    if not url:
        raise ValueError("engine http needs voice.http.url, e.g. http://studio.local:8810/v1/audio/speech")
    body: dict[str, Any] = {"model": cfg.get("model", "tts-1"), "input": text, "voice": cfg.get("voice", "default"),
                            "response_format": "wav", "speed": params.get("speed", 1.0)}
    if params.get("instructions") and cfg.get("instructions", True):
        body["instructions"] = params["instructions"]
    if cfg.get("mood_fields", True):
        body.update({k: params[k] for k in ("exaggeration", "cfg_weight", "base_speed") if k in params})
        if params.get("mood"):
            body["emotion"] = params["mood"]
    body.update(cfg.get("extra") or {})
    headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
    token = cfg.get("token") or os.environ.get(str(cfg.get("token_env", "PHRASEHOME_VOICE_TOKEN")), "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(str(url), data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(cfg.get("timeout", timeout))) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"voice server said {e.code}: {e.read()[:200].decode(errors='replace')}") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"couldn't reach the voice server at {url}: {e}") from e
    if not data:
        raise RuntimeError("the voice server sent back no audio")
    out.write_bytes(data)
    if data[:4] != b"RIFF":  # mp3 or similar: make it a wav
        if not shutil.which("ffmpeg"):
            raise RuntimeError("the voice server didn't send a wav, and ffmpeg isn't installed to convert it")
        raw = out.with_suffix(".src")
        os.replace(out, raw)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), str(out)], check=True,
                       capture_output=True, timeout=60)
        raw.unlink(missing_ok=True)


# ---------- after the engine: pitch, loudness, tempo ----------
def post_filter(post: dict, rate: int) -> str:
    """The ffmpeg -af chain for what the engine couldn't do itself ('' if nothing)."""
    parts = []
    pitch = float(post.get("pitch") or 0.0)
    tempo = float(post.get("tempo") or 1.0)
    if abs(pitch) >= 0.05:
        f = 2 ** (pitch / 12)
        parts += [f"asetrate={int(rate * f)}", f"aresample={rate}"]
        tempo /= f  # asetrate also speeds it up: undo that
    if abs(tempo - 1.0) >= 0.005:
        while tempo > 2.0:  # atempo takes 0.5..2 per step
            parts.append("atempo=2.0")
            tempo /= 2.0
        while tempo < 0.5:
            parts.append("atempo=0.5")
            tempo /= 0.5
        parts.append(f"atempo={tempo:.4f}")
    vol = float(post.get("volume") or 0.0)
    if abs(vol) >= 0.1:
        parts.append(f"volume={vol:+.1f}dB")
    return ",".join(parts)


def apply_post(wav: Path, post: dict) -> bool:
    """Shift pitch / tempo / loudness of a wav in place with ffmpeg. False if it couldn't."""
    try:
        with wave.open(str(wav)) as w:
            rate = w.getframerate()
    except (wave.Error, OSError, EOFError):
        return False
    chain = post_filter(post, rate)
    if not chain:
        return True
    if not shutil.which("ffmpeg"):
        _warn_once("ffmpeg", "the voice's pitch and loudness moods need ffmpeg; speaking without them")
        return False
    out = wav.with_name(wav.stem + ".mood.wav")
    r = subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(wav), "-af", chain, str(out)],
                       capture_output=True, timeout=60)
    if r.returncode == 0 and out.exists():
        os.replace(out, wav)
        return True
    out.unlink(missing_ok=True)
    log.warning("voice mood filter failed: %s", r.stderr.decode(errors="replace")[-200:])
    return False
