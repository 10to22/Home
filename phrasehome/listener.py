"""Microphone -> Vosk (offline speech recognition) -> Matcher -> Dispatcher.

All audio stays on this device. Nothing is sent anywhere unless a phrase matches,
and then only the command text goes to the backend.
"""

from __future__ import annotations

import json
import logging
import queue

from .config import Config
from .dispatcher import Dispatcher
from .matcher import Matcher, expand, vocabulary

log = logging.getLogger("phrasehome")


def listen(config: Config, dry_run: bool = False) -> None:
    try:
        import sounddevice as sd
        import vosk
    except (ImportError, OSError) as e:
        raise SystemExit(
            f"Audio dependencies missing ({e}). Run deploy/install.sh, or: "
            "sudo apt install libportaudio2 && pip install 'phrasehome[audio]'"
        )

    s = config.settings
    vosk.SetLogLevel(-1)
    model_dir = config.resolve_path(s.model_path)
    if not model_dir.exists():
        raise SystemExit(
            f"Vosk model not found at {model_dir}. Run deploy/install.sh or download one from "
            "https://alphacephei.com/vosk/models"
        )

    candidates = expand(config.commands, config.slots)
    matcher = Matcher(candidates, s.match_mode, s.match_threshold, s.block_words)
    model = vosk.Model(str(model_dir))
    if s.restrict_vocabulary:
        rec = vosk.KaldiRecognizer(model, s.sample_rate, json.dumps(vocabulary(candidates)))
    else:
        rec = vosk.KaldiRecognizer(model, s.sample_rate)
    rec.SetWords(True)

    dispatcher = Dispatcher(config, dry_run=dry_run)
    audio: queue.Queue[bytes] = queue.Queue(maxsize=200)

    def on_audio(indata, frames, time_info, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        try:
            audio.put_nowait(bytes(indata))
        except queue.Full:
            pass  # drop audio rather than fall behind

    log.info(
        "Listening for %d phrases (%d commands). Ctrl+C to stop.",
        len(candidates), len(config.commands),
    )
    try:
        with sd.RawInputStream(
            samplerate=s.sample_rate, blocksize=4000, device=s.device,
            dtype="int16", channels=1, callback=on_audio,
        ):
            while True:
                if rec.AcceptWaveform(audio.get()):
                    handle_result(json.loads(rec.Result()), matcher, dispatcher, config)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        dispatcher.close()


def handle_result(result: dict, matcher: Matcher, dispatcher: Dispatcher, config: Config) -> None:
    text = result.get("text", "").strip()
    if not text or text == "[unk]":
        return
    words = result.get("result") or []
    conf = sum(w.get("conf", 1.0) for w in words) / len(words) if words else 1.0
    if config.settings.log_heard:
        log.info("heard: %r (conf %.2f)", text, conf)
    if conf < config.settings.min_confidence:
        return
    m = matcher.match(text)
    if m and dispatcher.submit(m):
        log.info("MATCH %s  <- %r (score %.0f)", m.command.name, text, m.score)
