"""Microphones -> Vosk (offline speech recognition) -> Matcher -> Dispatcher.

Audio can come from the hub's own mic and/or ESP32 mic satellites around the house.
All audio stays on your network and is processed on the hub. Nothing is sent anywhere
unless a phrase matches, and then only the command text goes to the backend.
"""

from __future__ import annotations

import json
import re
import logging
import queue
from typing import Any, Callable

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
        from .understanding import CONFIRM_WORDS, candidates_with_aliases

        self.candidates = candidates_with_aliases(config, lang)  # + sentences taught on the History page
        self.matcher = Matcher(self.candidates, s.match_mode, s.match_threshold, s.block_words)
        words = vocabulary(self.candidates)
        words = words[:-1] + extra_words + CONFIRM_WORDS + words[-1:]  # wake names, yes/no
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
        st = config.settings
        self.cleaners: dict[str, Any] = {}  # per source: rumble filter + volume levelling
        self.vads: dict[str, Any] = {}
        self.spotters: dict[str, Any] = {}
        self.free: dict[str, Any] = {}  # full-vocabulary recognizers for live words (whisper mode)
        self._live: dict[str, tuple[str, float]] = {}
        self.whisper = None
        if str(st.recognizer).lower() == "whisper":
            try:
                from .hearing import Whisper

                self.whisper = Whisper(st.whisper_model)
                import threading as _th

                self._wq: queue.Queue = queue.Queue(maxsize=50)
                _th.Thread(target=self._whisper_worker, daemon=True).start()
                log.info("hearing with Whisper (%s)", self.whisper.name)
            except Exception as e:
                log.error("Whisper not started, using Vosk: %s", e)
        self._names = self.router.assistant.names if self.router else []

    def reload(self) -> None:
        """(Re)build phrases and grammars, e.g. after a command was added by voice."""
        extra = self.router.assistant.vocabulary() if self.router else []
        self.langs = {lang: _Lang(lang, m, self.config, extra) for lang, m in self.models.items()}
        self.recognizers: dict[str, tuple[str, object]] = {}
        self.buffers: dict[str, bytearray] = {}
        self._stops = None
        from .hearing import house_words

        names = list(self.router.assistant.names) if self.router else []
        self.prompt = house_words(self.config, names)  # primes Whisper with this house's words

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
        if source == "local" and self._hub_talking():
            self._while_talking(source, room, pcm, reply)
            return
        if self.config.settings.noise_cleanup:
            from .hearing import StreamCleaner

            pcm = self.cleaners.setdefault(source, StreamCleaner())(pcm)
        if self.whisper is not None:
            self._feed_whisper(source, room, pcm, reply)
            return
        rec = self._recognizer(source, room)
        L = self._lang(room)
        buf = self.buffers.setdefault(source, bytearray())
        buf += pcm
        if len(buf) > self.MAX_UTTERANCE_BYTES:
            del buf[: len(buf) - self.MAX_UTTERANCE_BYTES]
        woke = self._spot(source, room, pcm)
        if not rec.AcceptWaveform(pcm):
            self._partial(source, room, rec)
            return
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
        self._heard(source, room, text, result.get("result") or [])  # (not in privacy mode)
        confirm = getattr(self.dispatcher, "confirm", None)
        if confirm is not None and text and confirm.answer(room, text):
            return  # that was the yes/no to "did you say ...?"
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
                                                       person=person, lang=L.lang, woke=woke):
            return
        handle_result(result, L.matcher, self.dispatcher, self.config,
                      room=room, reply=reply, person=person)

    # ---------- live words, the AI's name, Whisper ----------
    def _partial(self, source: str, room: str | None, rec) -> None:
        """Words appearing on the dashboard while you're still talking."""
        if not self.config.settings.log_heard:
            return
        import time as _t

        text = json.loads(rec.PartialResult()).get("partial", "").strip()
        last, when = self._live.get(source, ("", 0.0))
        if text and text != last and _t.monotonic() - when > 0.15:
            self._live[source] = (text, _t.monotonic())
            self.dispatcher.events.live("hearing", room=room, text=text)

    def _heard(self, source: str, room: str | None, text: str, words: list) -> None:
        self._live.pop(source, None)
        if self.config.settings.log_heard and text:
            ws = [{"word": w.get("word", ""), "conf": round(float(w.get("conf", 1.0)), 2)} for w in words]
            conf = sum(w["conf"] for w in ws) / len(ws) if ws else None
            self.dispatcher.events.live("heard", room=room, text=text, words=ws,
                                        confidence=round(conf, 2) if conf is not None else None)

    def _spot(self, source: str, room: str | None, pcm: bytes) -> bool:
        """The dedicated "house" listener (only when the full vocabulary is on: with a restricted
        vocabulary the name is already one of very few words)."""
        if not self._names or (self.config.settings.restrict_vocabulary and self.whisper is None):
            return False
        import time as _t

        sp = self.spotters.get(source)
        if sp is None:
            from .hearing import WakeSpotter

            sp = self.spotters[source] = WakeSpotter(self._vosk, self._lang(room).model, self._names,
                                                     self.config.settings.sample_rate)
        now = _t.monotonic()
        sp.feed(pcm, now)
        return sp.heard_recently(now, window=3.0)

    def _free_recognizer(self, source: str, room: str | None):
        rec = self.free.get(source)
        if rec is None:
            rec = self.free[source] = self._vosk.KaldiRecognizer(self._lang(room).model, self.config.settings.sample_rate)
        return rec

    def _feed_whisper(self, source: str, room: str | None, pcm: bytes, reply: Reply | None) -> None:
        from .hearing import Vad

        self._spot(source, room, pcm)
        rec = self._free_recognizer(source, room)  # for the live words only
        if rec.AcceptWaveform(pcm):
            self._live.pop(source, None)
        else:
            self._partial(source, room, rec)
        vad = self.vads.get(source)
        if vad is None:
            vad = self.vads[source] = Vad(end_silence=self.config.settings.vad_silence)
        for clip in vad.feed(pcm):
            try:
                self._wq.put_nowait((source, room, clip, reply))
            except queue.Full:
                log.warning("too much to write down; skipped a sentence")

    def _whisper_worker(self) -> None:
        while True:
            item = self._wq.get()
            if item is None:
                return
            try:
                self._handle_clip(*item)
            except Exception as e:
                log.error("couldn't write that down: %s", e)

    def _handle_clip(self, source: str, room: str | None, clip: bytes, reply: Reply | None) -> None:
        import time as _t

        from .hearing import clean, sounds_like_name

        L = self._lang(room)
        audio = clean(clip) if self.config.settings.noise_cleanup else clip
        text, conf, words = self.whisper.transcribe(audio, self.prompt, L.lang)
        if not text:
            return
        result = {"text": text, "result": words}
        if self.dispatcher.state.is_private(room):
            m = L.matcher.match(text)
            a = m.command.action if m else {}
            if m and ((a.get("backend") == "privacy" and str(a.get("privacy")).lower() in ("off", "false"))
                      or (a.get("backend") == "freak" and str(a.get("freak")).lower() in ("off", "false", "stop"))):
                m.room, m.notify = room, reply
                self.dispatcher.submit(m)
            return
        self._heard(source, room, text, words)
        confirm = getattr(self.dispatcher, "confirm", None)
        if confirm is not None and confirm.answer(room, text):
            return
        person = self._who(clip, L, room)
        first = (re.findall(r"[a-zA-Z']+", text) or [""])[0]
        sp = self.spotters.get(source)
        woke = bool(sp and sp.heard_recently(_t.monotonic(), window=len(clip) / 32000 + 3)
                    and sounds_like_name(first, self._names))
        if self.router and self.router.handle(source, room, text, b"", reply, person=person, lang=L.lang,
                                              confidence=conf, woke=woke):
            return
        handle_result(result, L.matcher, self.dispatcher, self.config, room=room, reply=reply, person=person)

    def _who(self, clip: bytes, L, room: str | None) -> str | None:
        """Voice ID for a whole sentence (Whisper mode)."""
        vid = getattr(self.dispatcher, "voice_id", None)
        if vid is None or self.spk is None:
            return None
        rec = self._vosk.KaldiRecognizer(L.model, self.config.settings.sample_rate)
        rec.SetSpkModel(self.spk)
        rec.AcceptWaveform(clip)
        spk = json.loads(rec.FinalResult()).get("spk")
        if not spk:
            return None
        person, _score = vid.identify(spk, room)
        if person and getattr(self.dispatcher, "rooms", None) and room:
            self.dispatcher.rooms.saw(person, room, "voice")
        return person

    # ---------- "shut up" while the house is talking (the hub's own microphone) ----------
    def _hub_talking(self) -> bool:
        from .audio_out import local_busy

        return bool((self.config.sources.get("local_mic") or {}).get("mute_while_speaking", True)) and local_busy()

    def _stop_phrases(self) -> list[tuple[re.Pattern, Any]]:
        """Phrases that may cut in while the house talks: "shut up" and friends, alarm stop/snooze."""
        if getattr(self, "_stops", None) is None:
            out = []
            for cmd in self.config.commands:
                a = cmd.action
                if a.get("hush") or (a.get("backend") == "alarm" and str(a.get("alarm")).lower() in ("stop", "snooze")):
                    for p in cmd.phrases:
                        words = re.findall(r"[a-z']+", p.lower())
                        if words and "{" not in p:
                            out.append((re.compile(r"\b" + r"\s+".join(map(re.escape, words)) + r"\b"), cmd))
            out.sort(key=lambda x: -len(x[0].pattern))  # "stop talking" before "stop"
            self._stops = out
        return self._stops

    def _while_talking(self, source: str, room: str | None, pcm: bytes, reply: Reply | None) -> None:
        """The mic hears the house and you at once, so look for a stop phrase anywhere in what's heard
        so far (not a clean sentence), and act on it straight away, mid-word."""
        from . import audio_out

        rec = self._recognizer(source, room)
        if rec.AcceptWaveform(pcm):
            text = json.loads(rec.Result()).get("text", "")
        else:
            text = json.loads(rec.PartialResult()).get("partial", "")
        text = text.lower().strip()
        if not text:
            return
        said = " ".join(re.findall(r"[a-z']+", audio_out.speaking_text.lower()))
        for pattern, cmd in self._stop_phrases():
            if not pattern.search(text) or pattern.search(said):  # the house saying it isn't you saying it
                continue
            log.info("heard %r while talking: stopping", text)
            self.dispatcher.voice.hush()
            rec.Reset()
            self.buffers.get(source, bytearray()).clear()
            if cmd.action.get("backend") == "alarm":  # "stop" / "snooze" while the alarm talks
                from .matcher import Match

                self.dispatcher.submit(Match(command=cmd, phrase=cmd.phrases[0], slots={}, score=100.0,
                                             heard=text, room=room, confidence=1.0, notify=reply))
            return


def listen(config: Config, dry_run: bool = False) -> None:
    try:
        import vosk
    except ImportError as e:
        raise SystemExit(f"Vosk (the speech recognizer) isn't installed ({e}). In the phrasehome folder run:\n"
                         "  .venv/bin/pip install -e '.[audio]'\n(on the Pi, deploy/install.sh does it)")

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
            assistant.warm_up([dispatcher.default_room()])  # the first question shouldn't wait for loading
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

    room = opts.get("room")
    room = str(room).lower() if room else None
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
        r = opts.get("room")  # while the house talks, the engine only listens for "shut up" (_while_talking)
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
    if config.settings.log_heard:  # (off by default, for privacy)
        log.info("heard%s: %r (conf %.2f)", f" [{room}]" if room else "", text, conf)
    history = getattr(dispatcher, "history", None)
    if conf < config.settings.min_confidence:
        if history:
            history.add(kind="unclear", room=room, person=person, heard=text, confidence=round(conf, 2))
        return
    m = matcher.match(text)
    if m:
        m.room, m.confidence, m.notify, m.person = room, conf, reply, person
        run_or_confirm(m, dispatcher, config)
        return
    if history:
        history.add(kind="none", room=room, person=person, heard=text, confidence=round(conf, 2))
    near = matcher.near(text) if config.settings.did_you_mean else []
    if near:  # close to something: let the AI pick what was meant, without holding up listening
        import threading

        threading.Thread(target=_did_you_mean, args=(dispatcher, config, text, near, room, reply, person, conf),
                         daemon=True).start()


def run_or_confirm(m, dispatcher: Dispatcher, config: Config) -> None:
    """Run a matched command, or for a risky one the house isn't sure of, ask first."""
    from .understanding import is_risky, is_sure

    history = getattr(dispatcher, "history", None)
    confirm = getattr(dispatcher, "confirm", None)
    if confirm is not None and not m.confirmed and is_risky(m.command, config) and not is_sure(m):
        if history:
            history.add(kind="asked", room=m.room, person=m.person, heard=m.heard, matched=m.command.name,
                        confidence=round(m.confidence, 2), via=m.via)
        confirm.ask(m)
        return
    if history:
        history.add(kind="command", room=m.room, person=m.person, heard=m.heard, matched=m.command.name,
                    confidence=round(m.confidence, 2), score=round(m.score), via=m.via)
    if dispatcher.submit(m):
        log.info("MATCH %s <- %r (score %.0f%s%s)", m.command.name, m.heard, m.score,
                 f", {m.room}" if m.room else "", "" if m.via == "voice" else f", {m.via}")


def _did_you_mean(dispatcher, config, text, near, room, reply, person, conf) -> None:
    from .matcher import Match
    from .understanding import did_you_mean

    cand = did_you_mean(dispatcher, text, near)
    if cand is None:
        return
    log.info("did you mean %r? -> %s", text, cand.command.name)
    m = Match(cand.command, cand.text, dict(cand.slots), 80.0, text, room=room, person=person,
              confidence=conf, notify=reply, via="did-you-mean")
    dispatcher.events.live("meant", room=room, heard=text, command=cand.command.name)
    run_or_confirm(m, dispatcher, config)
