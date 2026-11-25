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


class _Lang:
    """Everything one spoken language needs: its model, phrases, matcher and grammar."""

    def __init__(self, lang: str, model, config: Config, extra_words: list[str]) -> None:
        s = config.settings
        self.lang = lang
        self.model = model
        self.candidates = expand(config.commands, config.slots, lang)
        self.matcher = Matcher(self.candidates, s.match_mode, s.match_threshold, s.block_words)
        words = vocabulary(self.candidates)
        words = words[:-1] + extra_words + words[-1:]  # the recognizer must know the wake names
        self.grammar = json.dumps(words) if s.restrict_vocabulary else None


class Engine:
    """One speech recognizer per audio source. Each room uses its language's model and phrases."""

    MAX_UTTERANCE_BYTES = 20 * 16000 * 2  # keep at most 20 s of audio per utterance

    def __init__(self, config: Config, model, dispatcher: Dispatcher, router=None,
                 models: dict | None = None) -> None:
        import vosk

        self._vosk = vosk
        self.config = config
        self.dispatcher = dispatcher
        self.router = router  # the AI assistant, if enabled
        self.models = {config.settings.language: model, **(models or {})}
        self.spk = None
        if getattr(dispatcher, "voice_id", None) is not None:
            try:
                self.spk = dispatcher.voice_id.spk_model()
            except Exception as e:
                log.error("voice recognition off: %s", e)
        self.reload()
        dispatcher.engine = self

    def reload(self) -> None:
        """(Re)build phrases and grammars, e.g. after a command was added by voice."""
        extra = self.router.assistant.vocabulary() if self.router else []
        self.langs = {lang: _Lang(lang, m, self.config, extra) for lang, m in self.models.items()}
        self.recognizers: dict[str, tuple[str, object]] = {}
        self.buffers: dict[str, bytearray] = {}

    @property
    def matcher(self) -> Matcher:
        return self.langs[self.config.settings.language].matcher

    @property
    def candidates(self):
        return self.langs[self.config.settings.language].candidates

    def _lang(self, room: str | None) -> _Lang:
        lang = self.dispatcher.language_for(room) if hasattr(self.dispatcher, "language_for") else None
        return self.langs.get(lang) or self.langs[self.config.settings.language]

    def _recognizer(self, source: str, room: str | None = None):
        L = self._lang(room)
        cur = self.recognizers.get(source)
        if cur is None or cur[0] != L.lang:
            rate = self.config.settings.sample_rate
            if L.grammar:
                rec = self._vosk.KaldiRecognizer(L.model, rate, L.grammar)
            else:
                rec = self._vosk.KaldiRecognizer(L.model, rate)
            rec.SetWords(True)
            if self.spk is not None:
                rec.SetSpkModel(self.spk)
            cur = self.recognizers[source] = (L.lang, rec)
        return cur[1]

    def feed(self, source: str, room: str | None, pcm: bytes, reply: Reply | None) -> None:
        smoke = getattr(self.dispatcher, "smoke", None)
        if smoke is not None:
            try:
                smoke.feed(room or source, pcm)  # beep patterns only, even in privacy mode
            except Exception as e:
                log.debug("smoke listener: %s", e)
        rec = self._recognizer(source, room)
        L = self._lang(room)
        buf = self.buffers.setdefault(source, bytearray())
        buf += pcm
        if len(buf) > self.MAX_UTTERANCE_BYTES:
            del buf[: len(buf) - self.MAX_UTTERANCE_BYTES]
        if rec.AcceptWaveform(pcm):
            result = json.loads(rec.Result())
            audio = bytes(buf)
            buf.clear()
            text = result.get("text", "").strip()
            if self.dispatcher.state.is_private(room):
                # Privacy mode: nothing is logged, matched or sent to the AI, except the
                # command that turns privacy off (otherwise you could never say it).
                m = L.matcher.match(text) if text else None
                a = m.command.action if m else {}
                if m and ((a.get("backend") == "privacy" and str(a.get("privacy")).lower() in ("off", "false"))
                          or (a.get("backend") == "freak" and str(a.get("freak")).lower() in ("off", "false", "stop"))):
                    m.room, m.notify = room, reply
                    self.dispatcher.submit(m)
                return
            person = None
            vid = getattr(self.dispatcher, "voice_id", None)
            if vid is not None and result.get("spk") and text:
                person, score = vid.identify(result["spk"], room)
                if person:
                    log.debug("voice: %s (%.2f)", person, score)
                    rooms = getattr(self.dispatcher, "rooms", None)
                    if rooms and room:
                        rooms.saw(person, room, "voice")
            if self.router and text and self.router.handle(source, room, text, audio, reply,
                                                           person=person, lang=L.lang):
                return
            handle_result(result, L.matcher, self.dispatcher, self.config,
                          room=room, reply=reply, person=person)


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
    if "voice_id" in config.extra:
        from .voiceid import VoiceID

        VoiceID(dispatcher)
    models = {}
    for lang, lcfg in (config.extra.get("languages") or {}).items():
        p = config.resolve_path((lcfg or {}).get("model_path", ""))
        if p.exists():
            models[lang] = vosk.Model(str(p))
        else:
            log.error("language %s: model not found at %s", lang, p)
    if (config.extra.get("smoke") or {}).get("enabled", True):
        from .smoke import SmokeListener

        SmokeListener(dispatcher)
    model = vosk.Model(str(model_dir))
    router = None
    if config.assistant and config.assistant.get("enabled", True):
        try:
            from .assistant import Assistant, AssistantRouter, Transcriber

            assistant = Assistant(dispatcher)
            dispatcher.assistant = assistant
            router = AssistantRouter(assistant, Transcriber(config.assistant, model, s.sample_rate,
                                                            models, s.language))
            log.info("AI assistant on: say \"%s, ...\"", assistant.names[0])
        except Exception as e:
            log.error("AI assistant not started: %s", e)
    engine = Engine(config, model, dispatcher, router, models)
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

    from .services import start_services

    services = start_services(dispatcher)
    scanners = []
    for sc in config.door_scanners:
        try:
            from .door_scanner import DoorScanner

            scanners.append(DoorScanner(dispatcher, sc))
        except Exception as e:
            log.error("door scanner for %s door not started: %s", sc.get("door", "front"), e)
    for sc in scanners:
        sc.start()

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
            dispatcher.satellites = server
        if not stream and not server and not hubs and not scanners and not services:
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
        for sc in scanners:
            sc.stop()
        for svc in services:
            svc.stop()
        dispatcher.close()


def _open_local_mic(config: Config, opts: dict, put):
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:
        raise SystemExit(
            f"Audio dependencies missing ({e}). On a Mac: .venv/bin/pip install -e '.[audio]' "
            "(and if that's not enough: brew install portaudio). On the Pi: deploy/install.sh"
        )
    import time as _time

    import numpy as np

    from .audio_out import local_busy

    room = opts.get("room")
    room = str(room).lower() if room else None
    mute = opts.get("mute_while_speaking", True)  # false if you use headphones
    # `opts` is the config's local_mic section: its room is read for every chunk, so moving the
    # laptop to another room on the dashboard (simulate) takes effect straight away
    check = {"until": _time.monotonic() + 10, "loudest": 0, "done": False}

    def on_audio(indata, frames, time_info, status) -> None:
        data = bytes(indata)
        if not check["done"]:  # a Mac without microphone permission gives silence, not an error
            check["loudest"] = max(check["loudest"], int(np.abs(np.frombuffer(data, dtype=np.int16)).max(initial=0)))
            if _time.monotonic() > check["until"]:
                check["done"] = True
                if check["loudest"] < 30:
                    log.warning("the microphone is only giving silence. On a Mac: System Settings > Privacy & "
                                "Security > Microphone > allow Terminal, then quit and reopen Terminal")
        if mute and local_busy():
            return  # the house is talking: don't listen to itself
        r = opts.get("room")
        put(("local", str(r).lower() if r else None, data, None))

    stream = sd.RawInputStream(
        samplerate=config.settings.sample_rate, blocksize=4000,
        device=opts.get("device", config.settings.device),
        dtype="int16", channels=1, callback=on_audio,
    )
    stream.start()
    log.info("local microphone on%s", f" ({room})" if room else "")
    return stream


def handle_result(result: dict, matcher: Matcher, dispatcher: Dispatcher, config: Config,
                  room: str | None = None, reply: Reply | None = None,
                  person: str | None = None) -> None:
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
        m.room, m.confidence, m.notify, m.person = room, conf, reply, person
        if dispatcher.submit(m):
            log.info("MATCH %s <- %r (score %.0f%s)", m.command.name, text, m.score,
                     f", {room}" if room else "")
