"""Door scanner: watches a camera at the door and reacts to faces.

    face seen -> recognized + allowed  -> say "retinal scan complete", ask for the fingerprint
                                          (never locks, so an unlocked door stays unlocked)
              -> recognized, not allowed -> say "retinal scan failed", lock the door
              -> unrecognized            -> add to the bank as a visitor, say "retinal scan
                                          failed", lock the door

What happens in each case is just a list of actions (`on_allowed`, `on_denied`, `on_unknown`) in
config.yaml, so you can add notifications, lights, music...

A decision needs the same answer in `confirm_frames` frames in a row, and the same person isn't
rescanned for `repeat_seconds`, so someone standing at the door doesn't trigger it every frame.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .faces import FaceBank, OpenCVFaceEngine

log = logging.getLogger("phrasehome.door")

DEFAULTS: dict[str, Any] = {
    "door": "front",
    "camera": 0,
    "interval": 0.3,
    "min_face_px": 80,
    "match_threshold": 0.40,  # cosine similarity; OpenCV suggests 0.363 for SFace
    "confirm_frames": 3,
    "repeat_seconds": 30,
    # Add unrecognized faces to the bank (with a photo) so you can approve them later.
    # Turn off if storing strangers' faces isn't OK where you live.
    "remember_unknown": True,
    "detector_model": "models/face/face_detection_yunet_2023mar.onnx",
    "recognizer_model": "models/face/face_recognition_sface_2021dec.onnx",
    "on_allowed": [{"backend": "voice", "speak": "retinal_scan_complete"},
                   {"backend": "voice", "speak": "fingerprint_prompt"}],
    "on_denied": [{"backend": "voice", "speak": "retinal_scan_failed"},
                  {"backend": "doors", "door": "{door}", "state": "locked"}],
    "on_unknown": [{"backend": "voice", "speak": "retinal_scan_failed"},
                   {"backend": "doors", "door": "{door}", "state": "locked"}],
}


class Camera:
    """camera: 0 (USB webcam), "rtsp://..." / MJPEG stream URL, or an ESP32-CAM snapshot URL
    ending in /capture or .jpg."""

    def __init__(self, spec: Any) -> None:
        import cv2

        self.cv2 = cv2
        self.spec = spec
        s = str(spec)
        self.snapshot = s.startswith("http") and (s.endswith("/capture") or s.endswith(".jpg"))
        self.cap = None if self.snapshot else cv2.VideoCapture(int(s) if s.isdigit() else s)

    def read(self):
        if self.snapshot:
            import requests

            r = requests.get(self.spec, timeout=5)
            r.raise_for_status()
            return self.cv2.imdecode(np.frombuffer(r.content, np.uint8), self.cv2.IMREAD_COLOR)
        ok, frame = self.cap.read()
        return frame if ok else None


@dataclass
class Decision:
    result: str  # allowed | denied | unknown
    person_id: str
    name: str
    score: float
    door: str
    photo: Path | None = None


@dataclass
class _Streak:
    label: str | None = None
    count: int = 0
    embeddings: list = field(default_factory=list)
    photo: bytes = b""
    photo_size: float = 0.0
    score: float = 0.0
    last_face: float = 0.0


class DoorScanner:
    def __init__(self, home, cfg: dict[str, Any], engine=None, camera=None) -> None:
        self.home = home
        self.cfg = {**DEFAULTS, **cfg}
        self.door = str(self.cfg["door"]).lower()
        c = home.config
        self.bank = FaceBank(Path(c.resolve_path(c.data_dir)) / "faces")
        self.engine = engine or OpenCVFaceEngine(c.resolve_path(self.cfg["detector_model"]),
                                                 c.resolve_path(self.cfg["recognizer_model"]))
        self._camera = camera
        self._streak = _Streak()
        self._last_decided: dict[str, float] = {}
        self._running = False

    # ---------- the decision logic (one camera frame at a time) ----------
    def process(self, img, now: float | None = None) -> Decision | None:
        now = time.monotonic() if now is None else now
        faces = [f for f in self.engine.faces(img) if f.size >= self.cfg["min_face_px"]]
        st = self._streak
        if not faces:
            if now - st.last_face > 2.0:
                self._streak = _Streak()  # they walked away
            return None
        face = max(faces, key=lambda f: f.size)  # whoever is closest
        emb = self.engine.embed(img, face)
        person, score = self.bank.best_match(emb)
        label = person.id if person and score >= self.cfg["match_threshold"] else "unknown"

        if label != st.label:
            st = self._streak = _Streak(label=label)
        st.count += 1
        st.last_face = now
        st.embeddings.append(emb)
        st.score = max(st.score, score if label != "unknown" else 0.0)
        if not st.photo or face.size >= 1.1 * st.photo_size:  # keep the closest, clearest shot
            st.photo = self._crop(img, face)
            st.photo_size = face.size
        if st.count < self.cfg["confirm_frames"]:
            return None
        if now - self._last_decided.get(label, -1e9) < self.cfg["repeat_seconds"]:
            return None  # still standing there: already handled

        self._last_decided[label] = now
        self._streak = _Streak(label=label, last_face=now)
        mean = np.mean(st.embeddings, axis=0)
        mean = mean / (np.linalg.norm(mean) + 1e-9)
        if label == "unknown":
            result = "unknown"
            if self.cfg["remember_unknown"]:
                person = self.bank.add_visitor(mean, st.photo)
                self._last_decided[person.id] = now  # don't re-trigger on their new bank entry
            else:
                from .faces import Person

                person = Person("unknown", "unknown visitor", False, "visitor")
                st.photo = b""
        else:
            person = self.bank.people[label]
            self.bank.seen(person, mean if person.kind == "visitor" else None)
            result = "allowed" if person.allowed else "denied"

        photo = self.bank.save_photo("scans", st.photo) if st.photo else None
        d = Decision(result, person.id, person.name, round(st.score, 3), self.door, photo)
        self._react(d)
        return d

    def _crop(self, img, face) -> bytes:
        try:
            return self.engine.crop_jpeg(img, face)
        except Exception:
            return b""

    def _react(self, d: Decision) -> None:
        log.info("door scan at %s: %s (%s, score %.2f)", d.door, d.result, d.name, d.score)
        self.home.events.log("scan", door=d.door, result=d.result, person=d.person_id,
                             name=d.name, score=d.score, photo=str(d.photo) if d.photo else None)
        if d.result == "allowed":
            self.home.events.log("scan.awaiting_fingerprint", door=d.door, person=d.person_id)
        values = {"name": d.name, "door": d.door, "person": d.person_id, "score": d.score}
        for step in self.cfg[f"on_{d.result}"]:
            try:
                self.home.run_action(step, values, phrase=f"door scan {d.result}")
            except Exception as e:
                log.error("door scan step %s failed: %s", step, e)

    # ---------- camera loop ----------
    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("door scanner watching the %s door (camera %s), %d people in the bank",
                 self.door, self.cfg["camera"], len(self.bank.people))

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        backoff = 1.0
        while self._running:
            try:
                if self._camera is None:
                    self._camera = Camera(self.cfg["camera"])
                img = self._camera.read()
                if img is not None:
                    self.process(img)
                backoff = 1.0
                time.sleep(self.cfg["interval"])
            except Exception as e:
                log.warning("door camera problem: %s (retrying in %.0fs)", e, backoff)
                self._camera = None
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    # ---------- enrollment helper ----------
    def capture_embeddings(self, count: int = 8, timeout: float = 20.0) -> tuple[list, bytes]:
        """Grab `count` face samples from the camera (for `phrasehome faces enroll --camera`)."""
        cam = self._camera or Camera(self.cfg["camera"])
        embs, photo, end = [], b"", time.monotonic() + timeout
        while len(embs) < count and time.monotonic() < end:
            img = cam.read()
            if img is None:
                continue
            faces = [f for f in self.engine.faces(img) if f.size >= self.cfg["min_face_px"]]
            if faces:
                f = max(faces, key=lambda f: f.size)
                embs.append(self.engine.embed(img, f))
                photo = photo or self._crop(img, f)
            time.sleep(0.4)  # small pauses so you can turn your head between samples
        return embs, photo
