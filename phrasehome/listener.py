"""Microphones -> Vosk (offline speech recognition) -> Matcher -> Dispatcher.

Audio can come from the hub's own mic and/or ESP32 mic satellites around the house.
All audio stays on your network and is processed on the hub. Nothing is sent anywhere
unless a phrase matches, and then only the command text goes to the backend.
"""

from __future__ import annotations

import json
import logging
import queue
from typing import Callable

from .config import Config
from .dispatcher import Dispatcher
from .matcher import Matcher, expand, vocabulary

log = logging.getLogger("phrasehome")

Reply = Callable[[str], None]


class Engine:
    """One speech recognizer per audio source, all sharing one model and matcher."""

    def __init__(self, config: Config, model, dispatcher: Dispatcher) -> None:
        import vosk

        self._vosk = vosk
        self.config = config
        self.model = model
        self.dispatcher = dispatcher
        s = config.settings
        self.candidates = expand(config.commands, config.slots)
        self.matcher = Matcher(self.candidates, s.match_mode, s.match_threshold, s.block_words)
        self.grammar = json.dumps(vocabulary(self.candidates)) if s.restrict_vocabulary else None
        self.recognizers: dict[str, object] = {}

    def _recognizer(self, source: str):
        rec = self.recognizers.get(source)
        if rec is None:
            rate = self.config.settings.sample_rate
            if self.grammar:
                rec = self._vosk.KaldiRecognizer(self.model, rate, self.grammar)
            else:
                rec = self._vosk.KaldiRecognizer(self.model, rate)
            rec.SetWords(True)
            self.recognizers[source] = rec
        return rec

    def feed(self, source: str, room: str | None, pcm: bytes, reply: Reply | None) -> None:
        rec = self._recognizer(source)
        if rec.AcceptWaveform(pcm):
            handle_result(json.loads(rec.Result()), self.matcher, self.dispatcher, self.config,
                          room=room, reply=reply)


def listen(config: Config, dry_run: bool = False) -> None:
    try:
        import vosk
    except ImportError as e:
        raise SystemExit(f"Vosk missing ({e}). Run deploy/install.sh or pip install 'phrasehome[audio]'")

    s = config.settings
    vosk.SetLogLevel(-1)
    model_dir = config.resolve_path(s.model_path)
    if not model_dir.exists():
        raise SystemExit(
            f"Vosk model not found at {model_dir}. Run deploy/install.sh or download one from "
            "https://alphacephei.com/vosk/models"
        )

    dispatcher = Dispatcher(config, dry_run=dry_run)
    engine = Engine(config, vosk.Model(str(model_dir)), dispatcher)
    audio: queue.Queue[tuple[str, str | None, bytes, Reply | None]] = queue.Queue(maxsize=2000)

    def put(item) -> None:
        try:
            audio.put_nowait(item)
        except queue.Full:
            pass  # drop audio rather than fall behind

    from .speakers import get_hub

    hubs = [get_hub(n, b, config) for n, b in config.backends.items() if b["type"] == "speakers"]
    for h in hubs:
        h.start()

    stream = server = None
    try:
        local = config.sources.get("local_mic")
        if local is not None and local.get("enabled", True):
            stream = _open_local_mic(config, local, put)
        sats = config.sources.get("satellites")
        if sats is not None and sats.get("enabled", True):
            from .satellites import SatelliteServer

            server = SatelliteServer(
                lambda src, room, pcm, reply: put((src, room, pcm, reply)),
                host=sats.get("host", "0.0.0.0"), port=int(sats.get("port", 7777)),
                allowed_ips=sats.get("allowed_ips"),
            )
            server.start()
        if not stream and not server and not hubs:
            raise SystemExit("no audio sources enabled (see `sources:` in config)")

        log.info("Listening for %d phrases (%d commands). Ctrl+C to stop.",
                 len(engine.candidates), len(config.commands))
        while True:
            engine.feed(*audio.get())
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        if stream:
            stream.close()
        if server:
            server.close()
        for h in hubs:
            h.close()
        dispatcher.close()


def _open_local_mic(config: Config, opts: dict, put):
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:
        raise SystemExit(
            f"Audio dependencies missing ({e}). Run deploy/install.sh, or: "
            "sudo apt install libportaudio2 && pip install 'phrasehome[audio]'"
        )
    room = opts.get("room")
    room = str(room).lower() if room else None

    def on_audio(indata, frames, time_info, status) -> None:
        put(("local", room, bytes(indata), None))

    stream = sd.RawInputStream(
        samplerate=config.settings.sample_rate, blocksize=4000,
        device=opts.get("device", config.settings.device),
        dtype="int16", channels=1, callback=on_audio,
    )
    stream.start()
    log.info("local microphone on%s", f" ({room})" if room else "")
    return stream


def handle_result(result: dict, matcher: Matcher, dispatcher: Dispatcher, config: Config,
                  room: str | None = None, reply: Reply | None = None) -> None:
    text = result.get("text", "").strip()
    if not text or text == "[unk]":
        return
    words = result.get("result") or []
    conf = sum(w.get("conf", 1.0) for w in words) / len(words) if words else 1.0
    if config.settings.log_heard:
        log.info("heard%s: %r (conf %.2f)", f" [{room}]" if room else "", text, conf)
    if conf < config.settings.min_confidence:
        return
    m = matcher.match(text)
    if m:
        m.room, m.confidence, m.notify = room, conf, reply
        if dispatcher.submit(m):
            log.info("MATCH %s <- %r (score %.0f%s)", m.command.name, text, m.score,
                     f", {room}" if room else "")
