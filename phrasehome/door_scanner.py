"""Door scanner: watches a camera at the door and reacts to faces.

    face seen -> recognized + allowed  -> say "retinal scan complete", ask for the fingerprint
                                          (never locks, so an unlocked door stays unlocked)
              -> recognized, not allowed -> say "retinal scan failed", lock the door
              -> unrecognized            -> add to the bank as a visitor, say "retinal scan
                                          failed", lock the door

What happens in each case is just a list of actions (`on_allowed`, `on_denied`, `on_unknown`) in
config.yaml, so you can add notifications, lights, music...

`auto_unlock: [Finn]` (names or ids, or `true` for everyone allowed), or `phrasehome faces
auto-unlock Finn`, which saves it in the face bank: those people's faces unlock
the door on their own, with no fingerprint, in any mode (away, lockdown, vacation, alarm armed),
even if their bank entry was set to not allowed or expired. If the door is already unlocked,
nothing changes. If the unlock fails, it is retried. Runs `on_auto_unlock`.

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
    # A face this close to an enrolled person (but under match_threshold) is "not sure yet":
    # keep looking instead of calling it a stranger and locking them out.
    "unsure_margin": 0.10,
    "confirm_frames": 3,
    "repeat_seconds": 30,
    # Add unrecognized faces to the bank (with a photo) so you can approve them later.
    # Turn off if storing strangers' faces isn't OK where you live.
    "remember_unknown": True,
    "detector_model": "models/face/face_detection_yunet_2023mar.onnx",
    "recognizer_model": "models/face/face_recognition_sface_2021dec.onnx",
    "on_allowed": [{"backend": "voice", "speak": "retinal_scan_complete"},
                   {"backend": "voice", "speak": "fingerprint_prompt"}],
    "auto_unlock": [],
    "on_auto_unlock": [{"backend": "voice", "speak": "retinal_scan_complete"}],  # after unlocking
    "on_denied": [{"backend": "voice", "speak": "retinal_scan_failed"},
                  {"backend": "doors", "door": "{door}", "state": "locked"}],
    "on_unknown": [{"backend": "voice", "speak": "retinal_scan_failed"},
                   {"backend": "doors", "door": "{door}", "state": "locked"}],
    # doorbell button on the door camera (firmware/door_cam DOORBELL_PIN)
    "on_doorbell": [{"backend": "voice", "speak": "doorbell", "everywhere": True},
                    {"backend": "voice", "speak": "doorbell_announce", "everywhere": True}],
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
        if self.cap is not None and not self.cap.isOpened():
            hint = (" On a Mac: System Settings > Privacy & Security > Camera > allow Terminal, then "
                    "quit and reopen Terminal." if s.isdigit() else "")
            raise RuntimeError(f"can't open camera {s}.{hint}")
        self.doorbell: int | None = None  # press counter reported by the ESP32-CAM

    def read(self):
        if self.snapshot:
            import requests

            r = requests.get(self.spec, timeout=5)
            r.raise_for_status()
            if "X-Doorbell" in r.headers:
                self.doorbell = int(r.headers["X-Doorbell"])
            return self.cv2.imdecode(np.frombuffer(r.content, np.uint8), self.cv2.IMREAD_COLOR)
        ok, frame = self.cap.read()
        return frame if ok else None


class FeedCamera:
    """Reads the shared camera feed (vision.CameraHub) instead of opening the camera again."""

    def __init__(self, feed) -> None:
        self.feed = feed
        self._last = 0.0

    @property
    def doorbell(self):
        return self.feed.doorbell

    def read(self):
        frame, _, t = self.feed.latest()
        if frame is None or t == self._last:
            return None
        self._last = t
        return frame


@dataclass
class Decision:
    result: str  # allowed | denied | unknown
    person_id: str
    name: str
    score: float
    door: str
    photo: Path | None = None
    auto_unlock: bool = False  # allowed, and on the auto_unlock list: unlocked with no fingerprint


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
        self._bell: int | None = None
        self._running = False
        self.last_seen: tuple = (None, 0.0, None)  # (closest enrolled name, score, label)

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
        label, person, score, near, near_score = self.identify(emb)
        self.last_seen = (near.name if near else None, near_score, label)
        if label == "unsure":
            st.last_face = now
            return None  # almost someone we know: keep looking, don't decide on this frame

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
        auto = False
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
            auto = self.auto_unlocks(person)  # no matter what: their face opens the door
            result = "allowed" if auto or person.is_allowed() else "denied"

        photo = self.bank.save_photo("scans", st.photo) if st.photo else None
        d = Decision(result, person.id, person.name, round(st.score, 3), self.door, photo,
                     auto_unlock=label != "unknown" and auto)
        if label == "unknown" or result == "denied":
            near, near_score, _ = self.last_seen
            if near:
                log.info("closest enrolled face was %s at %.2f (needs %.2f). If that was them, run "
                         "`phrasehome faces test --webcam` and enroll more samples in this light.",
                         near, near_score, self.cfg["match_threshold"])
        self._react(d)
        return d

    def identify(self, emb) -> tuple[str, Any, float, Any, float]:
        """(label, person, score, closest enrolled person, their score). Enrolled people always
        win over visitor entries, so a visitor saved from your own face can't lock you out."""
        thr = float(self.cfg["match_threshold"])
        known, k_score = self.bank.best_match(emb, kind="enrolled")
        if known and k_score >= thr:
            return known.id, known, k_score, known, k_score
        if known and k_score >= thr - float(self.cfg["unsure_margin"]):
            return "unsure", None, k_score, known, k_score
        visitor, v_score = self.bank.best_match(emb, kind="visitor")
        if visitor and v_score >= thr:
            return visitor.id, visitor, v_score, known, k_score
        return "unknown", None, k_score, known, k_score

    def auto_unlocks(self, person) -> bool:
        if person.kind == "visitor":
            return False
        if getattr(person, "auto_unlock", False):  # `phrasehome faces auto-unlock Finn`
            return True
        who = self.cfg.get("auto_unlock")
        if who is True:
            return person.kind != "visitor" and person.is_allowed()
        if not who:
            return False
        names = {str(w).lower() for w in ([who] if isinstance(who, str) else who)}
        return person.kind != "visitor" and (person.id.lower() in names or person.name.lower() in names)

    def _unlock(self, d: "Decision") -> None:
        for attempt in range(3):
            try:
                self.home.doors.set(d.door, "unlocked", reason=f"face: {d.name}")
            except Exception as e:
                log.error("auto-unlock of the %s door failed (try %d): %s", d.door, attempt + 1, e)
            if self.home.dry_run or self.home.doors.state(d.door) != "locked":
                return
            time.sleep(1.0)
        log.error("couldn't unlock the %s door for %s after 3 tries", d.door, d.name)

    def _crop(self, img, face) -> bytes:
        try:
            return self.engine.crop_jpeg(img, face)
        except Exception:
            return b""

    def _react(self, d: Decision) -> None:
        log.info("door scan at %s: %s (%s, score %.2f)", d.door, d.result, d.name, d.score)
        self.home.events.log("scan", door=d.door, result=d.result, person=d.person_id,
                             name=d.name, score=d.score, photo=str(d.photo) if d.photo else None,
                             auto_unlock=d.auto_unlock)
        if d.result == "allowed" and not d.auto_unlock:
            self.home.events.log("scan.awaiting_fingerprint", door=d.door, person=d.person_id,
                                 name=d.name)
        values = {"name": d.name, "door": d.door, "person": d.person_id, "score": d.score,
                  "photo": str(d.photo) if d.photo else "",
                  "here": self.home.config.doors.get(d.door, {}).get("room")}
        if d.auto_unlock:
            self._unlock(d)  # first, before any talking
        for step in self.cfg["on_auto_unlock" if d.auto_unlock else f"on_{d.result}"]:
            try:
                self.home.run_action(step, values, phrase=f"door scan {d.result}")
            except Exception as e:
                log.error("door scan step %s failed: %s", step, e)

    def doorbell_pressed(self, img=None) -> None:
        photo = ""
        if img is not None:
            try:
                import cv2

                ok, buf = cv2.imencode(".jpg", img)
                if ok:
                    photo = str(self.bank.save_photo("doorbell", buf.tobytes()))
            except Exception:
                pass
        log.info("doorbell at the %s door", self.door)
        self.home.events.log("doorbell", door=self.door, photo=photo)
        values = {"door": self.door, "photo": photo}
        threading.Thread(target=self.home.run_steps, args=(self.cfg["on_doorbell"], values, "doorbell"),
                         daemon=True).start()

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
                    spec = self.cfg["camera"]
                    hub = getattr(self.home, "cameras", None)
                    feed = hub.get(str(spec)) if hub else None
                    if feed:
                        self._camera = FeedCamera(feed)
                    elif isinstance(spec, str) and not spec.isdigit() and "://" not in spec:
                        time.sleep(1)  # a camera name: wait for the shared camera feed to start
                        continue
                    else:
                        self._camera = Camera(spec)
                img = self._camera.read()
                bell = getattr(self._camera, "doorbell", None)
                if bell is not None:
                    if self._bell is not None and bell > self._bell:
                        self.doorbell_pressed(img)
                    self._bell = bell
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
    def capture_embeddings(self, count: int = 20, timeout: float | None = None,
                           progress=None) -> tuple[list, bytes]:
        """Grab `count` face samples from the camera (for `phrasehome faces enroll --camera`).
        Gives up after `timeout` seconds (default: about 1.5 s per sample, at least 20 s).
        `progress(n, count)` is called after each sample."""
        timeout = max(20.0, count * 1.5) if timeout is None else timeout
        spec = self.cfg["camera"]
        cams = getattr(self.home.config, "cameras", {}) or {}
        if isinstance(spec, str) and spec.lower() in cams:  # a camera name from `cameras:`
            spec = cams[spec.lower()].get("url", 0)
        cam = self._camera or Camera(spec)
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
                if progress:
                    progress(len(embs), count)
            time.sleep(0.4)  # small pauses so you can turn your head between samples
        return embs, photo
