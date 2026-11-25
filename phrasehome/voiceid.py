"""Who's talking: offline voice recognition with Vosk's speaker model (x-vectors).

voice_id:
  model_path: models/vosk-model-spk-0.4
  threshold: 0.55        # cosine similarity needed to name someone; raise if it mixes people up

Teach it your voice: on the dashboard's Voices card pick your name and press "Learn my voice",
then say any six sentences. Or say "this is Sam" after speaking normally in a room (it learns from
your last few sentences there), or `phrasehome voice-id record Sam`, or from recordings:
  phrasehome voice-id enroll Sam clip1.wav clip2.wav
When it's sure who's talking, it keeps learning (so it adapts to the mic and the room). When two
people sound too alike for a sentence, it says nobody rather than guess (`margin`).

It's for personal touches ("remind me" in the living room goes to Sam's reminders, music, energy
credit), not security: voices are easier to imitate than faces or fingerprints.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("phrasehome.voiceid")


class VoiceID:
    def __init__(self, home) -> None:
        self.home = home
        cfg = dict(home.config.extra.get("voice_id") or {})
        self.cfg = cfg
        self.threshold = float(cfg.get("threshold", 0.55))
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "voiceprints.json"
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError):
            raw = {}
        self.prints: dict[str, list[np.ndarray]] = {p: [np.array(v, np.float32) for v in vs] for p, vs in raw.items()}
        self.recent: dict[str, list[tuple[float, np.ndarray]]] = {}  # room -> recent utterance vectors
        self.margin = float(cfg.get("margin", 0.05))  # the best must beat the next person by this much
        self.keep = int(cfg.get("samples", 40))  # newest samples kept per person
        self.learning: dict | None = None
        self.learning_total = 0
        self._saved = time.time()
        self._spk_model = None
        home.voice_id = self

    def spk_model(self):
        if self._spk_model is None:
            import vosk

            p = self.home.config.resolve_path(self.cfg.get("model_path", "models/vosk-model-spk-0.4"))
            if not Path(p).exists():
                raise FileNotFoundError(f"speaker model missing: {p} (run deploy/install.sh)")
            self._spk_model = vosk.SpkModel(str(p))
        return self._spk_model

    @staticmethod
    def _unit(v) -> np.ndarray:
        v = np.asarray(v, np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def scores(self, v) -> dict[str, float]:
        """How much this sentence sounds like each person: their average voice and their closest
        few samples together (one odd sample can't win on its own)."""
        out = {}
        for person, vs in self.prints.items():
            if not vs:
                continue
            m = np.stack(vs)
            sims = np.sort(m @ v)[::-1]
            centroid = self._unit(m.mean(axis=0))
            out[person] = 0.5 * float(centroid @ v) + 0.5 * float(sims[:3].mean())
        return out

    def identify(self, vector, room: str | None = None) -> tuple[str | None, float]:
        v = self._unit(vector)
        now = time.time()
        if room:
            self.recent.setdefault(room, []).append((now, v))
            self.recent[room] = [(t, x) for t, x in self.recent[room] if now - t < 90][-6:]
        if self.learning:  # "learn my voice": the next few sentences are this person
            person, left = self.learning["person"], self.learning["left"] - 1
            self._add(person, v)
            self.learning = {**self.learning, "left": left} if left > 0 else None
            self.home.events.live("voice_learning", person=person, done=self.learning_total - max(left, 0),
                                  total=self.learning_total)
            if left <= 0:
                self._save()
                self.home.events.log("voice.learned", person=person, samples=len(self.prints[person]))
            return person, 1.0
        ranked = sorted(self.scores(v).items(), key=lambda x: -x[1])
        if not ranked:
            return None, 0.0
        best, score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        if score < self.threshold or score - second < self.margin:
            return None, score  # nobody, or too close to call between two people
        if score >= self.threshold + 0.1 and score - second >= 2 * self.margin:
            self._add(best, v)  # sure: keep learning their voice (adapts to the mic and the room)
            if now - self._saved > 60:
                self._save()
        return best, score

    def _add(self, person: str, v) -> None:
        vs = self.prints.setdefault(person.lower(), [])
        vs.append(self._unit(v))
        self.prints[person.lower()] = vs[-self.keep:]

    def _save(self) -> None:
        self._saved = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({p: [x.tolist() for x in v] for p, v in self.prints.items()}))

    def start_learning(self, person: str, sentences: int = 6) -> None:
        """The next `sentences` things anyone says (near any microphone) are this person's voice."""
        self.learning = {"person": person.lower(), "left": int(sentences)}
        self.learning_total = int(sentences)
        self.home.events.live("voice_learning", person=person.lower(), done=0, total=self.learning_total)

    def forget(self, person: str) -> bool:
        gone = self.prints.pop(person.lower(), None) is not None
        self._save()
        return gone

    def status(self) -> dict:
        return {"people": {p: len(v) for p, v in self.prints.items()},
                "learning": dict(self.learning, total=self.learning_total) if self.learning else None}

    def enroll(self, person: str, vectors) -> int:
        for v in vectors:
            self._add(person, v)
        self._save()
        return len(self.prints[person.lower()])

    def enroll_recent(self, person: str, room: str) -> int:
        """'This is Sam': learn from the last few sentences said in this room."""
        vecs = [v for t, v in self.recent.get(room, []) if time.time() - t < 90]
        if not vecs:
            raise ValueError("say a couple of sentences first, then 'this is <name>'")
        self.recent[room] = []
        return self.enroll(person, vecs)

    def vector_from_wav(self, path: str):
        import wave

        import vosk

        with wave.open(path) as w:
            rec = vosk.KaldiRecognizer(self._model_for_wav(), w.getframerate())
            rec.SetSpkModel(self.spk_model())
            while True:
                data = w.readframes(4000)
                if not data:
                    break
                rec.AcceptWaveform(data)
            res = json.loads(rec.FinalResult())
        if "spk" not in res:
            raise ValueError(f"{path}: too short to learn a voice from (speak for 3+ seconds)")
        return res["spk"]

    def _model_for_wav(self):
        import vosk

        if getattr(self, "_wav_model", None) is None:
            self._wav_model = vosk.Model(str(self.home.config.resolve_path(self.home.config.settings.model_path)))
        return self._wav_model

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass
