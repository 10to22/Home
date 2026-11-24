"""Who's talking: offline voice recognition with Vosk's speaker model (x-vectors).

voice_id:
  model_path: models/vosk-model-spk-0.4
  threshold: 0.55        # cosine similarity needed to name someone; raise if it mixes people up

Teach it your voice by saying "this is Sam" (or "I'm Sam") a few times after speaking normally in
a room; it learns from your last few sentences there. Or from recordings:
  phrasehome voice-id enroll Sam clip1.wav clip2.wav

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

    def identify(self, vector, room: str | None = None) -> tuple[str | None, float]:
        v = self._unit(vector)
        if room:
            self.recent.setdefault(room, []).append((time.time(), v))
            self.recent[room] = [(t, x) for t, x in self.recent[room] if time.time() - t < 90][-6:]
        best, score = None, 0.0
        for person, vs in self.prints.items():
            s = max(float(v @ x) for x in vs)
            if s > score:
                best, score = person, s
        return (best, score) if score >= self.threshold else (None, score)

    def enroll(self, person: str, vectors) -> int:
        person = person.lower()
        vs = self.prints.setdefault(person, [])
        for v in vectors:
            vs.append(self._unit(v))
        self.prints[person] = vs[-30:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({p: [x.tolist() for x in v] for p, v in self.prints.items()}))
        return len(self.prints[person])

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
            rec = vosk.KaldiRecognizer(self.home.voice_id._model_for_wav(), w.getframerate())
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
