"""Microphones -> Vosk (offline speech recognition) -> Matcher -> Dispatcher.

Audio can come from the hub's own mic and/or ESP32 mic satellites around the house.
All audio stays on your network and is processed on the hub. Nothing is sent anywhere
unless a phrase matches, and then only the command text goes to the backend.
"""

from __future__ import annotations

import json
import sys
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
        from .wordbank import FILLER_WORDS

        self.candidates = candidates_with_aliases(config, lang)  # + sentences taught on the History page
        from .hearing_train import trained_fixes

        self.matcher = Matcher(self.candidates, s.match_mode, s.match_threshold, s.block_words,
                               phonetic=bool(getattr(s, 'phonetic_match', True)),
                               fixes=trained_fixes(config) if lang.startswith("en") else None)
        # Vosk's fixed vocabulary: the most common wordings as whole phrases (a small, fast graph), and every
        # word of every wording, so "whack the telly on" can still be written down and matched
        short = candidates_with_aliases(config, lang, for_grammar=True) if s.restrict_vocabulary else self.candidates
        phrases = vocabulary(short)[:-1]
        more_words = [w for w in sorted({w for c in self.candidates for w in c.words}) if w not in set(phrases)]
        words = phrases + more_words + ["[unk]"]
        # wake names, yes/no, and words that change a command's meaning: "please" and "can you" (so
        # they're heard, then ignored), "and"/"then" (two commands at once), and "not"/"don't"
        # (so "don't turn on the lights" is heard as that, and blocked)
        heard_too = list(extra_words) + CONFIRM_WORDS
        if lang.startswith("en"):
            heard_too += FILLER_WORDS + ["and", "then"] + CORRECTION_WORDS
        heard_too += [w for b in s.block_words for w in b.lower().split()]
        have = set(words)
        heard_too = [w for w in dict.fromkeys(heard_too) if w not in have]
        words = words[:-1] + heard_too + words[-1:]
        find = getattr(model, "find_word", None) or getattr(model, "vosk_model_find_word", None)
        if find is not None and s.restrict_vocabulary:
            # words the speech model has never heard of can't be recognised: leave them (and phrases with
            # them) out, instead of Vosk warning about each one. `phrasehome train-hearing` lists them.
            try:
                known: dict[str, bool] = {}

                def ok(w: str) -> bool:
                    if w not in known:
                        known[w] = find(w) >= 0
                    return known[w]

                words = [x for x in words if x == "[unk]" or all(ok(w) for w in x.split())]
            except Exception as e:
                log.debug("vocabulary check: %s", e)
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
        self._word_conf: dict[str, float] = {}  # how sure the recognizer was of the last sentence, per source
        if router is not None:
            router.try_command = self._try_command  # "house, lock the front door" runs as a command
        self.whisper = None
        if str(st.recognizer).lower() == "whisper":
            try:
                from .hearing import Whisper

                from .speech_tuning import whisper_beam

                self.whisper = Whisper(st.whisper_model, beam=whisper_beam(self.config))
                import threading as _th

                self._wq: queue.Queue = queue.Queue(maxsize=50)
                _th.Thread(target=self._whisper_worker, daemon=True).start()
                log.info("hearing with Whisper (%s)", self.whisper.name)
                self._set_hotwords()
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
        self._set_hotwords()

    def _set_hotwords(self) -> None:
        """Whisper's hotwords: the house's names and the people's names, kept short. reload() runs before
        Whisper exists (first start), so this runs again once it does."""
        w = getattr(self, "whisper", None)
        if w is None:
            return
        names = list(self.router.assistant.names) if self.router else []
        w.hotwords = ", ".join(([*names] + [i.name for i in self.config.instances])[:20])

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

    # ---------- a second look at near misses ----------
    def _second_guess(self, text: str, audio: bytes, room: str | None, L) -> str | None:
        """`text` ran nothing but was close to a command (and short): the recognizer's other guesses for the
        same audio, then Whisper's, if one of them is a command. Measured on 1,800 clips: full vocabulary
        83% -> 89.5% right, and no false triggers on real talk and music."""
        st = self.config.settings
        words = text.split()
        if not audio or len(words) > 10 or L.matcher.match(text) is not None:
            return None
        if not L.matcher.near(text, floor=55, n=1):
            return None  # nothing like a command: chat, not a misheard command
        n = int(getattr(st, "alternatives", 5) or 0)
        # with a fixed vocabulary the first guess is already the best (measured: 98.3% either way)
        if n > 1 and not L.grammar and getattr(self, "_vosk", None) is not None:
            try:
                rec = (self._vosk.KaldiRecognizer(L.model, st.sample_rate, L.grammar) if L.grammar
                       else self._vosk.KaldiRecognizer(L.model, st.sample_rate))
                rec.SetMaxAlternatives(n)
                for i in range(0, len(audio), 8000):
                    rec.AcceptWaveform(audio[i:i + 8000])
                for a in (json.loads(rec.FinalResult()).get("alternatives") or [])[:n]:
                    alt = self._plus().fix_text(str(a.get("text", "")).strip())
                    if alt and alt != text and L.matcher.match(alt) is not None:
                        return alt
            except Exception as e:
                log.debug("alternatives: %s", e)
        w = self._second_whisper()
        if w is not None:
            try:
                from .hearing import clean

                alt, conf, _ = w.transcribe(clean(audio) if st.noise_cleanup else audio, self.prompt, L.lang)
                from .matcher import normalize

                alt = self._plus().fix_text(normalize(alt))
                if alt and conf >= st.min_confidence and L.matcher.match(alt) is not None:
                    return alt
            except Exception as e:
                log.debug("second opinion: %s", e)
        return None

    def _second_whisper(self):
        """Whisper as a second opinion while Vosk does the listening (loaded the first time it's needed)."""
        mode = str(getattr(self.config.settings, "second_opinion", "auto")).lower()
        if mode in ("off", "none", "false", "no") or getattr(self, "whisper", None) is not None:
            return None
        if mode == "auto" and sys.platform != "darwin":
            return None
        w = self.__dict__.get("_whisper2")
        if w is None and not self.__dict__.get("_whisper2_failed"):
            try:
                from .hearing import Whisper
                from .speech_tuning import whisper_beam

                w = self.__dict__["_whisper2"] = Whisper(self.config.settings.whisper_model, beam=whisper_beam(self.config))
                log.info("Whisper (%s) will double-check near misses", w.name)
            except Exception as e:
                self.__dict__["_whisper2_failed"] = True
                log.info("no second opinion from Whisper (%s)", e)
        return w

    def feed(self, source: str, room: str | None, pcm: bytes, reply: Reply | None) -> None:
        smoke = getattr(self.dispatcher, "smoke", None)
        if smoke is not None:
            try:
                smoke.feed(room or source, pcm)  # beep patterns only, even in privacy mode
            except Exception as e:
                log.debug("smoke listener: %s", e)
        life = getattr(self.dispatcher, "life", None)
        if life is not None and not self._hub_talking():
            try:
                life.noise.level(room or source, pcm)  # loudness only, for quiet hours
            except Exception as e:
                log.debug("noise meter: %s", e)
        plus = self._plus()
        pcm = plus.pre(source, room, pcm)  # mic health, echo cancelling, the room's noise, direction
        if not pcm:
            return
        if source == "local" and self._hub_talking() and not plus.echo_on(source):
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
        text = plus.fix_text(result.get("text", "").strip())  # learned accent fixes
        result["text"] = text
        if text and not self.dispatcher.state.is_private(room):
            better = self._second_guess(text, audio, room, L)
            if better and better != text:
                log.info("heard %r; the next best guess %r is a command", text, better)
                result = {**result, "text": better, "alternative_of": text}
                text = better
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
        self._heard(source, room, text, result.get("result") or [], person)  # (not in privacy mode)
        unknown = bool(vid is not None and result.get("spk") and text and person is None)
        ws = result.get("result") or []
        self.__dict__.setdefault("_word_conf", {})[source] = sum(w.get("conf", 1.0) for w in ws) / len(ws) if ws else 1.0
        confirm = getattr(self.dispatcher, "confirm", None)
        if confirm is not None and text and confirm.answer(room, text):
            return  # that was the yes/no to "did you say ...?"
        if self.router and text and self.router.handle(source, room, text, audio, reply,
                                                       person=person, lang=L.lang, woke=woke):
            return
        handle_result(result, L.matcher, self.dispatcher, self.config,
                      room=room, reply=reply, person=person, unknown_voice=unknown,
                      needs_name=plus.name_needed(source, room, person))
        plus.after(audio)  # keep the clip of a misheard sentence, if asked to

    def _plus(self):
        """hearing_plus.py: made on first use (also for engines built without __init__ in tests)."""
        p = self.__dict__.get("_hearing_plus")
        if p is None:
            from .hearing_plus import HearingPlus

            p = self.__dict__["_hearing_plus"] = HearingPlus(self)
        return p

    def _try_command(self, text: str, room: str | None, reply: Reply | None, person: str | None = None,
                     lang: str | None = None, confidence: float | None = None, source: str | None = None) -> bool:
        """Run `text` as a voice command if it is one (for the AI router: what was said after the
        house's name, or the whole sentence). True if it ran or was asked about."""
        from .matcher import normalize

        words = normalize(text).split()
        if not words or "unk" in words:  # not heard clearly enough to be sure: let the AI write it down
            return False
        L = self.langs.get(lang) or self._lang(room)
        if confidence is None:
            confidence = getattr(self, "_word_conf", {}).get(source or "")
        conf = 1.0 if confidence is None else float(confidence)
        if conf < self.config.settings.min_confidence:
            return False
        found = find_commands(text, L.matcher, self.dispatcher, self.config, room, allow_teach=True)
        if found is None:
            return False
        matches, delays, say = found
        if say:
            self.dispatcher.voice.speak(say, room=room)
            return True
        run_all(matches, delays, self.dispatcher, self.config, room, reply, person, conf)
        return True

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

    def _heard(self, source: str, room: str | None, text: str, words: list, person: str | None = None) -> None:
        self._live.pop(source, None)
        if self.config.settings.log_heard and text:
            ws = [{"word": w.get("word", ""), "conf": round(float(w.get("conf", 1.0)), 2)} for w in words]
            conf = sum(w["conf"] for w in ws) / len(ws) if ws else None
            self.dispatcher.events.live("heard", room=room, text=text, words=ws, person=person,
                                        confidence=round(conf, 2) if conf is not None else None)

    def _spot(self, source: str, room: str | None, pcm: bytes) -> bool:
        """The dedicated "house" listener (only when the full vocabulary is on: with a restricted
        vocabulary the name is already one of very few words)."""
        st = self.config.settings
        engine = str(getattr(st, "wake_engine", "vosk")).lower()
        if engine in ("off", "none", "false"):
            return False
        oww = engine == "openwakeword"
        if not oww and (not self._names or (st.restrict_vocabulary and self.whisper is None)):
            return False
        import time as _t

        sp = self.spotters.get(source)
        if sp is None:
            if oww:
                from .hearing import OpenWakeWordSpotter

                try:
                    sp = OpenWakeWordSpotter(st.wake_models, st.wake_threshold, st.sample_rate)
                except Exception as e:
                    log.error("wake word engine not started, using the speech recognizer: %s", e)
                    st.wake_engine = "vosk"
                    return self._spot(source, room, pcm)
                self.spotters[source] = sp
            else:
                from .hearing import WakeSpotter

                sp = self.spotters[source] = WakeSpotter(self._vosk, self._lang(room).model, self._names,
                                                         st.sample_rate)
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
        text = self._plus().fix_text(text)
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
        person = self._who(clip, L, room)
        self._heard(source, room, text, words, person)
        confirm = getattr(self.dispatcher, "confirm", None)
        if confirm is not None and confirm.answer(room, text):
            return
        first = next((w for w in re.findall(r"[a-zA-Z']+", text) if w.lower() not in ("hey", "ok", "okay", "hi")), "")
        sp = self.spotters.get(source)
        woke = bool(sp and sp.heard_recently(_t.monotonic(), window=len(clip) / 32000 + 3)
                    and sounds_like_name(first, self._names))
        self.__dict__.setdefault("_word_conf", {})[source] = conf if conf is not None else 1.0
        if self.router and self.router.handle(source, room, text, b"", reply, person=person, lang=L.lang,
                                              confidence=conf, woke=woke):
            return
        unknown = bool(self.__dict__.pop("_voice_heard", False) and person is None)
        plus = self._plus()
        handle_result(result, L.matcher, self.dispatcher, self.config, room=room, reply=reply, person=person,
                      unknown_voice=unknown, needs_name=plus.name_needed(source, room, person))
        plus.after(clip)

    def _who(self, clip: bytes, L, room: str | None) -> str | None:
        """Voice ID for a whole sentence (Whisper mode)."""
        vid = getattr(self.dispatcher, "voice_id", None)
        if vid is None or self.spk is None:
            return None
        rec = self._vosk.KaldiRecognizer(L.model, self.config.settings.sample_rate)
        rec.SetSpkModel(self.spk)
        rec.AcceptWaveform(clip)
        spk = json.loads(rec.FinalResult()).get("spk")
        self.__dict__["_voice_heard"] = bool(spk)
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
                  person: str | None = None, unknown_voice: bool = False, needs_name: str | None = None) -> None:
    """unknown_voice: voice ID heard a voice it doesn't know (a guest, the TV). needs_name: why a command
    without the name is ignored right now (the TV is on, or the voice came from its direction)."""
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
    found = find_commands(text, matcher, dispatcher, config, room)
    matches, delays, say = found if found is not None else ([], [], None)
    if say:
        dispatcher.voice.speak(say, room=room)
        return
    if matches and needs_name:
        from .attention import is_safety_command

        if not all(is_safety_command(m.command) for m in matches):
            log.info("not running %r without the name: %s", text, needs_name)
            if history:
                history.add(kind="needs_name", room=room, heard=text, matched=matches[0].command.name,
                            why=needs_name, confidence=round(conf, 2))
            return
    if matches and unknown_voice:
        from .attention import is_safety_command, strangers_need_name

        if strangers_need_name(dispatcher) and not all(is_safety_command(m.command) for m in matches):
            log.info("a voice I don't know said %r: commands need the name (voice_id: strangers_need_name)", text)
            if history:
                history.add(kind="stranger", room=room, heard=text, matched=matches[0].command.name,
                            confidence=round(conf, 2))
            return
    if matches:
        flipped = bool(result.get("alternative_of")) and \
            (set(text.split()) & POLARITY) != (set(str(result["alternative_of"]).split()) & POLARITY)
        for m in matches:
            m.unsure = flipped or polarity_unsure(m, words, config)
        run_all(matches, delays, dispatcher, config, room, reply, person, conf)
        return
    if history:
        history.add(kind="none", room=room, person=person, heard=text, confidence=round(conf, 2))
    near = matcher.near(text) if config.settings.did_you_mean else []
    if near:  # close to something: let the AI pick what was meant, without holding up listening
        import threading

        threading.Thread(target=_did_you_mean, args=(dispatcher, config, text, near, room, reply, person, conf),
                         daemon=True).start()


_JOINERS = re.compile(r"\s+(?:and then|and also|and|then|also|plus)\s+")


def find_commands(text: str, matcher: Matcher, dispatcher, config: Config, room: str | None,
                  allow_teach: bool = False):
    """Everything `text` asks for: (matches, delay in seconds for each, something to say) or None.
    A correction ("no, I meant the bedroom"), then understanding_plus.py (numbers, "brighter", "turn them
    off", "upstairs", "... in five minutes", "when I say ..." when allow_teach), then plain commands."""
    fix = correction(text, matcher, dispatcher, config, room)
    if fix:
        return fix, [0.0] * len(fix), None
    if _UNDO.match(text.lower().strip(" .!")):
        return undo_last(dispatcher, config, room)
    teaching = re.match(r"^(?:when|whenever|if) (?:i|we) say ", text.lower())
    try:
        from .understanding_plus import understand

        # making a new command needs the house's name: "when I say ..." in conversation isn't for it
        got = None if (teaching and not allow_teach) else understand(text, matcher, dispatcher, config, room)
    except Exception as e:
        log.error("understanding %r: %s", text, e)
        got = None
    if got is not None:
        return got
    plain = commands_in(text, matcher, config)
    return (plain, [0.0] * len(plain), None) if plain else None


def run_all(matches: list, delays: list, dispatcher, config: Config, room, reply, person, conf: float) -> None:
    """Run each match now, or later for "... in five minutes"."""
    from . import clock

    for m, delay in zip(matches, delays or [0.0] * len(matches)):
        m.room, m.confidence, m.notify, m.person = room, conf, reply, person
        if delay > 0:
            log.info("%s in %.0f s", m.command.name, delay)
            dispatcher.timeline.call_in(delay, lambda m=m: run_or_confirm(m, dispatcher, config))
            try:
                dispatcher.voice.speak(f"Okay, {clock.spoken_duration(delay)} from now.", room=room)
            except Exception as e:
                log.debug("say later: %s", e)
        else:
            run_or_confirm(m, dispatcher, config)


def commands_in(text: str, matcher: Matcher, config: Config | None = None) -> list:
    """The command in `text`, or several: "lock the front door and turn off the lights" is two.
    Every part has to be a command (different ones), otherwise it's treated as one sentence."""
    m = matcher.match(text)
    if m:
        return [m]
    if config is not None and not getattr(config.settings, "multi_commands", True):
        return []
    from .matcher import normalize

    parts = [p for p in _JOINERS.split(normalize(text)) if p.strip()]
    if len(parts) < 2 or len(parts) > 4:
        return []
    out = []
    for part in parts:
        pm = matcher.match(part)
        if pm is None or any(x.command.name == pm.command.name for x in out):
            return []
        pm.heard = normalize(text)
        out.append(pm)
    return out


# ---------- "undo that" ----------
_UNDO = re.compile(r"^(?:undo|undo that|undo it|take that back|reverse that|put it back|change it back|"
                   r"undo the last (?:thing|command))(?: please)?$")


def undo_last(dispatcher, config: Config, room: str | None):
    """The opposite of the last command in this room (lights on -> off). Locks, the alarm, modes and
    routines are never undone by voice."""
    import time as _t

    from .matcher import Match

    last = (dispatcher.__dict__.get("_last_command") or {}).get(room)
    if not last or _t.monotonic() - last[0] > 300:
        return [], [], "There's nothing I can undo."
    prev = last[1]
    opp = _opposite(prev.command, config)
    if opp is None:
        return [], [], "I can't undo that one by voice."
    return [Match(opp, opp.phrases[0], dict(prev.slots), 90.0, "undo", via="undo")], [0.0], None


# ---------- "no, I meant the bedroom" ----------
CORRECTION_WINDOW = 25.0  # seconds after a command
_CORRECTION = re.compile(r"^(?:(?:no|nope|nah|oops|sorry|actually|wait|wrong one|not that one)\b[ ,]*)+"
                         r"|^(?:i meant|i mean|i said|meant)\b\s*")
CORRECTION_WORDS = ["no", "nope", "oops", "sorry", "actually", "wait", "wrong", "one", "i", "meant", "mean",
                    "said", "not", "that", "the", "instead"]


def correction(text: str, matcher: Matcher, dispatcher, config: Config, room: str | None) -> list:
    """Right after a command: "no, I meant the bedroom" runs it again with the bedroom; "actually, turn
    off the bedroom lights" runs that. [] when it isn't a correction."""
    import time as _t

    from .matcher import Match, normalize

    said = normalize(text)
    if not _CORRECTION.match(said):
        return []
    last = (dispatcher.__dict__.get("_last_command") or {}).get(room)
    if not last or _t.monotonic() - last[0] > CORRECTION_WINDOW:
        return []
    prev = last[1]
    rest = said
    while True:  # "no no, I meant ..." -> "..."
        m = _CORRECTION.match(rest)
        if not m or not m.group(0):
            break
        rest = rest[m.end():].strip()
    rest = re.sub(r"\s+instead$", "", rest)
    if not rest:
        return []
    whole = matcher.match(rest)  # a whole new command
    if whole is not None:
        whole.heard, whole.via = said, "correction"
        return [whole]
    # just the slot: "the bedroom", "not the kitchen, the bedroom"
    found: tuple[int, str, str] | None = None  # (position, slot, value)
    for name, old in prev.slots.items():
        if name == "here":
            continue
        for spoken, value in (config.slots.get(name) or {}).items():
            if value == old:
                continue
            for mm in re.finditer(rf"\b{re.escape(normalize(spoken))}\b", rest):
                if found is None or mm.start() > found[0]:  # the last one said wins ("not X, Y")
                    found = (mm.start(), name, value)
    if found is None:
        return []
    _, name, value = found
    slots = {**prev.slots, name: value}
    phrase = prev.phrase
    log.info("correction: %s with %s=%s instead of %s", prev.command.name, name, value, prev.slots.get(name))
    out = []
    undo = _opposite(prev.command, config) if getattr(config.settings, "correction_undo", True) else None
    if undo is not None:  # "kitchen lights on" was wrong: turn them back off first
        log.info("correction: undoing with %s", undo.name)
        out.append(Match(undo, undo.phrases[0], dict(prev.slots), 90.0, said, via="correction"))
    out.append(Match(prev.command, phrase, slots, 90.0, said, via="correction"))
    return out


_FLIP = {"on": "off", "off": "on", "true": "false", "false": "true", "start": "stop", "stop": "start",
         "up": "down", "down": "up", "open": "close", "close": "open"}


def _flipped(v):
    if isinstance(v, bool):
        return not v
    if isinstance(v, str):
        if v.lower() in _FLIP:
            return _FLIP[v.lower()]
        return re.sub(r"\b(on|off)\b", lambda m: _FLIP[m.group(1)], v)
    if isinstance(v, dict):
        return {k: _flipped(x) for k, x in v.items()}
    return v


POLARITY = {"on", "off", "up", "down", "open", "close", "shut", "lock", "unlock", "start", "stop", "arm", "disarm"}


def polarity_unsure(m, words: list[dict], config: Config) -> bool:
    """The word that makes this command the opposite of another ("lights *off*") was heard unclearly, and
    getting it wrong would do the opposite thing. Training with 1,800 clips found "off" heard as "on" (and
    "power up" as "power of") as the worst mistakes: these get "did you say ...?" instead of a guess."""
    limit = float(getattr(config.settings, "polarity_confidence", 0.85) or 0)
    if limit <= 0 or not words:
        return False
    from .understanding import is_risky

    said = set(m.phrase.split()) & POLARITY
    if not said:
        return False
    shaky = any(str(w.get("word", "")).lower() in POLARITY and float(w.get("conf", 1.0)) < limit for w in words)
    return shaky and (_opposite(m.command, config) is not None or is_risky(m.command, config))


def _opposite(cmd, config: Config):
    """The command that undoes `cmd` (lights on <-> lights off), if the config has one. Never for locks,
    the alarm or routines: those stay as they are."""
    from .understanding import is_risky

    a = cmd.action
    if is_risky(cmd, config) or a.get("backend") in ("routine", "doors", "security", "mode", "freak", "privacy"):
        return None
    want = _flipped(a)
    if want == a:
        return None
    return next((c for c in config.commands if c.name != cmd.name and c.action == want), None)


def run_or_confirm(m, dispatcher: Dispatcher, config: Config) -> None:
    """Run a matched command, or for a risky one the house isn't sure of, ask first."""
    from .understanding import is_risky, is_sure

    history = getattr(dispatcher, "history", None)
    confirm = getattr(dispatcher, "confirm", None)
    if confirm is not None and not m.confirmed and (getattr(m, "unsure", False)
                                                    or (is_risky(m.command, config) and not is_sure(m))):
        if history:
            history.add(kind="asked", room=m.room, person=m.person, heard=m.heard, matched=m.command.name,
                        confidence=round(m.confidence, 2), via=m.via)
        confirm.ask(m)
        return
    if history:
        history.add(kind="command", room=m.room, person=m.person, heard=m.heard, matched=m.command.name,
                    confidence=round(m.confidence, 2), score=round(m.score), via=m.via, slots=dict(m.slots))
    if dispatcher.submit(m):
        import time as _t

        dispatcher.__dict__.setdefault("_last_command", {})[m.room] = (_t.monotonic(), m)
        levels = dispatcher.__dict__.setdefault("_levels", {})  # for "brighter" / "a bit warmer"
        for slot, value in (m.slots or {}).items():
            levels[(m.command.name, m.room, slot)] = levels[(m.command.name, None, slot)] = value
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
