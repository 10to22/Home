"""Cameras: live feeds, person/vehicle/animal detection, "what's at the door?", package watch.

cameras:
  front door: { url: http://phrasehome-doorcam-front.local/capture, room: front door, roles: [door, porch] }
  driveway:   { url: http://phrasehome-cam-driveway.local/capture, roles: [perimeter] }
  back yard:  { url: rtsp://user:pass@192.168.1.60/stream1, roles: [perimeter] }
  living room: { url: 0, roles: [pet, indoor] }      # a USB webcam on the hub

One background reader per camera keeps the newest picture, shared by the dashboard's live view,
the door scanner and the AI, so cheap ESP32-CAMs aren't overloaded.

Detection runs on the hub (OpenCV + MobileNet-SSD, no cloud): a quick motion check first, then
the detector. Events:  camera {camera, room, detected: person|vehicle|animal, label, photo}
Package watch (roles: [porch]) asks the AI to look at the porch after someone leaves it:
package.delivered / package.collected / package.taken.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger("phrasehome.vision")

VOC = ["background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus", "car", "cat", "chair",
       "cow", "diningtable", "dog", "horse", "motorbike", "person", "pottedplant", "sheep", "sofa",
       "train", "tvmonitor"]
GROUPS = {"person": {"person"}, "vehicle": {"car", "bus", "motorbike", "bicycle"},
          "animal": {"cat", "dog", "bird", "horse", "sheep", "cow"}}


def group_of(label: str) -> str | None:
    return next((g for g, labels in GROUPS.items() if label in labels), None)


class Detector:
    """MobileNet-SSD (Caffe) through OpenCV's DNN module. ~0.2 s per frame on a Pi 5."""

    def __init__(self, prototxt: Path, model: Path, min_confidence: float = 0.55) -> None:
        import cv2

        for p in (prototxt, model):
            if not Path(p).exists():
                raise FileNotFoundError(f"detector model missing: {p} (run deploy/install.sh)")
        self.cv2 = cv2
        self.net = cv2.dnn.readNetFromCaffe(str(prototxt), str(model))
        self.min_conf = min_confidence

    def detect(self, img) -> list[dict[str, Any]]:
        h, w = img.shape[:2]
        blob = self.cv2.dnn.blobFromImage(self.cv2.resize(img, (300, 300)), 0.007843, (300, 300), 127.5)
        self.net.setInput(blob)
        out = self.net.forward()
        found = []
        for i in range(out.shape[2]):
            conf = float(out[0, 0, i, 2])
            cls = int(out[0, 0, i, 1])
            if conf < self.min_conf or cls >= len(VOC):
                continue
            label = VOC[cls]
            g = group_of(label)
            if g:
                x1, y1, x2, y2 = (out[0, 0, i, 3:7] * np.array([w, h, w, h])).astype(int)
                found.append({"group": g, "label": label, "confidence": round(conf, 2),
                              "box": [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]})
        return found


class MotionGate:
    """Cheap check so the detector only runs when something in the picture changed."""

    def __init__(self, threshold: float = 0.004) -> None:
        self.prev = None
        self.threshold = threshold

    def moved(self, img) -> bool:
        import cv2

        g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(img, (160, 120)), cv2.COLOR_BGR2GRAY), (5, 5), 0)
        prev, self.prev = self.prev, g
        if prev is None:
            return True
        diff = cv2.absdiff(prev, g)
        return float((diff > 25).mean()) > self.threshold


class CameraFeed:
    def __init__(self, name: str, cfg: dict[str, Any]) -> None:
        self.name = name
        self.cfg = cfg
        self.url = cfg.get("url", cfg.get("camera", 0))
        self.room = str(cfg.get("room", name)).lower()
        self.roles = {str(r).lower() for r in cfg.get("roles", [])}
        self.interval = float(cfg.get("interval", 0.5))
        self.frame = None
        self.jpeg: bytes = b""
        self.time = 0.0
        self.doorbell: int | None = None
        self.error = ""
        self._cam = None
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> None:
        self._running = True
        if str(self.url) == "sim:webcam":
            return  # fed by the laptop's webcam when it's placed here (simulator.Placement)
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        from .door_scanner import Camera

        backoff = 1.0
        while self._running:
            try:
                if self._cam is None:
                    self._cam = Camera(self.url)
                frame = self._cam.read()
                if frame is not None:
                    self.set_frame(frame, getattr(self._cam, "doorbell", None))
                    self.error = ""
                backoff = 1.0
                time.sleep(self.interval)
            except Exception as e:
                if not self.error:
                    log.warning("camera %s: %s", self.name, e)
                self.error = str(e)
                self._cam = None
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

    def set_frame(self, frame, doorbell: int | None = None) -> None:
        import cv2

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        with self._lock:
            self.frame = frame
            self.jpeg = buf.tobytes() if ok else self.jpeg
            self.time = time.time()
            if doorbell is not None:
                self.doorbell = doorbell

    def latest(self):
        with self._lock:
            return self.frame, self.jpeg, self.time

    def online(self) -> bool:
        return time.time() - self.time < 15


class CameraHub:
    def __init__(self, home) -> None:
        self.home = home
        cfg = home.config.cameras
        self.feeds = {str(n).lower(): CameraFeed(str(n).lower(), dict(c or {})) for n, c in cfg.items()}
        self.settings = home.config.camera_settings
        self.detect_every = float(self.settings.get("detect_seconds", 1.0))
        self.cooldown = float(self.settings.get("cooldown_seconds", 60))
        self._detector = None
        self._gates = {n: MotionGate() for n in self.feeds}
        self._last_seen: dict[tuple[str, str], float] = {}
        self._porch: dict[str, dict[str, Any]] = {n: {"person_at": 0.0, "package": False,
                                                       "checked": 0.0}
                                                  for n, f in self.feeds.items() if "porch" in f.roles}
        self._running = False
        home.cameras = self

    # ---------- access ----------
    def get(self, name: str | None) -> CameraFeed | None:
        if not name:
            return None
        return self.feeds.get(str(name).lower())

    def find(self, role: str) -> list[CameraFeed]:
        return [f for f in self.feeds.values() if role in f.roles]

    def snapshot(self, name: str) -> bytes:
        f = self.get(name)
        if f is None:
            raise KeyError(f"no camera called '{name}' (known: {', '.join(self.feeds)})")
        if not f.jpeg or time.time() - f.time > 20:
            raise RuntimeError(f"the {name} camera isn't sending pictures ({f.error or 'offline'})")
        return f.jpeg

    # ---------- AI looks at a camera ----------
    def describe(self, name: str, question: str = "") -> str:
        from . import ai

        q = question or "Describe what you see, in one or two short spoken sentences."
        return ai.ask(self.home, f"This is the house's {name} camera, right now. {q} "
                      "Say who or what is there (e.g. delivery driver, neighbor, cat, a parked car). "
                      "Don't guess anyone's identity or personal details.", images=[self.snapshot(name)])

    # ---------- detection loop ----------
    def detector(self):
        if self._detector is None:
            c = self.home.config
            self._detector = Detector(c.resolve_path(self.settings.get("prototxt", "models/detect/deploy.prototxt")),
                                      c.resolve_path(self.settings.get("model", "models/detect/mobilenet_iter_73000.caffemodel")),
                                      float(self.settings.get("min_confidence", 0.55)))
        return self._detector

    def start(self) -> None:
        if not self.feeds:
            return
        for f in self.feeds.values():
            f.start()
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("cameras: %s", ", ".join(self.feeds))

    def stop(self) -> None:
        self._running = False
        for f in self.feeds.values():
            f.stop()

    def _loop(self) -> None:
        while self._running:
            for name, f in self.feeds.items():
                if f.cfg.get("detect", True) is False:
                    continue
                frame, _, t = f.latest()
                if frame is None or time.time() - t > 5:
                    continue
                try:
                    self.analyze(name, frame)
                except FileNotFoundError as e:
                    log.error("%s; camera detection off", e)
                    return
                except Exception as e:
                    log.warning("camera %s analysis: %s", name, e)
            self.porch_tick()
            time.sleep(self.detect_every)

    def analyze(self, name: str, frame, found: list[dict] | None = None, now: float | None = None) -> list[dict]:
        """Run detection on one frame and log new sightings. `found` lets tests skip the model."""
        now = time.time() if now is None else now
        f = self.feeds[name]
        if found is None:
            if not self._gates[name].moved(frame):
                return []
            found = self.detector().detect(frame)
        wanted = set(f.cfg.get("detect_groups", ["person", "vehicle", "animal"]))
        new = []
        for g in sorted({d["group"] for d in found if d["group"] in wanted}):
            best = max((d for d in found if d["group"] == g), key=lambda d: d["confidence"])
            if g == "person" and name in self._porch:
                self._porch[name]["person_at"] = now
            if now - self._last_seen.get((name, g), -1e9) < self.cooldown:
                self._last_seen[(name, g)] = now
                continue
            self._last_seen[(name, g)] = now
            photo = self._save(name, frame)
            self.home.events.log("camera", camera=name, room=f.room, detected=g, label=best["label"],
                                 confidence=best["confidence"], photo=photo)
            new.append(best)
        return new

    def _save(self, name: str, frame) -> str:
        try:
            import cv2

            from .faces import FaceBank

            ok, buf = cv2.imencode(".jpg", frame)
            if not ok:
                return ""
            c = self.home.config
            bank = FaceBank(Path(c.resolve_path(c.data_dir)) / "faces")
            return str(bank.save_photo(f"cameras/{name.replace(' ', '-')}", buf.tobytes()))
        except Exception:
            return ""

    # ---------- package watch ----------
    PACKAGE_SCHEMA = {"type": "object", "additionalProperties": False,
                      "required": ["package_visible", "description"],
                      "properties": {"package_visible": {"type": "boolean"},
                                     "description": {"type": "string"}}}

    def check_package(self, name: str) -> dict:
        from . import ai

        return ai.ask(self.home, "This is a doorstep/porch camera. Is there a delivered package, box, "
                      "bag or envelope left on the ground or porch (not being carried by a person)? "
                      "Description: a few words, e.g. 'a medium brown box by the mat', or empty.",
                      images=[self.snapshot(name)], schema=self.PACKAGE_SCHEMA)

    def porch_tick(self, now: float | None = None) -> None:
        """Look for a package once someone has left the porch, and recheck while one is there."""
        now = time.time() if now is None else now
        recheck = float(self.settings.get("package_recheck_minutes", 15)) * 60
        for name, st in self._porch.items():
            if st["person_at"] and now - st["person_at"] > 15:  # they left 15+ s ago
                fresh = now - st["person_at"] < 300
                st["person_at"] = 0.0
                if not fresh:
                    continue
            elif not (st["package"] and now - st["checked"] > recheck):
                continue
            st["checked"] = now
            try:
                res = self.check_package(name)
            except Exception as e:
                log.warning("package check failed: %s", e)
                continue
            self._package_result(name, st, bool(res["package_visible"]), res.get("description", ""), now)

    def _package_result(self, name: str, st: dict, visible: bool, desc: str, now: float) -> None:
        if visible and not st["package"]:
            st["package"] = True
            f = self.feeds[name]
            frame, _, _ = f.latest()
            photo = self._save(name, frame) if frame is not None else ""
            self.home.events.log("package.delivered", camera=name, description=desc, photo=photo)
        elif not visible and st["package"]:
            st["package"] = False
            recent = [e for e in self.home.events.query(limit=80)
                      if e["kind"] in ("presence.arrived", "fingerprint.accepted", "door.unlocked")
                      or (e["kind"] == "scan" and e.get("result") == "allowed")]
            cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - 1800))
            kind = "package.collected" if any(e["time"] >= cutoff for e in recent) else "package.taken"
            self.home.events.log(kind, camera=name)
