"""Face recognition + the face bank.

The bank (data/faces/bank.json) holds everyone the door camera has seen:
  - people you enrolled, each `allowed: true` or false
  - unknown visitors, added automatically as "visitor-1", "visitor-2"... with a photo, so you
    can approve them later (`phrasehome faces allow visitor-3 --name Grandma`)
Every scan is also logged with a snapshot in data/faces/scans/.

Recognition uses OpenCV's YuNet face detector and SFace recognizer (small ONNX models that run
fine on a Raspberry Pi). Everything stays on the hub; no face data leaves your house.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

MAX_EMBEDDINGS = 60  # per person; more photos = better recognition from different angles


@dataclass
class Face:
    box: tuple[int, int, int, int]  # x, y, w, h
    size: float  # min(w, h) in pixels: how close the person is
    score: float  # detector confidence
    raw: Any = None


class OpenCVFaceEngine:
    def __init__(self, detector: Path, recognizer: Path, min_detect_score: float = 0.85) -> None:
        import cv2

        for p in (detector, recognizer):
            if not Path(p).exists():
                raise FileNotFoundError(f"face model missing: {p} (run deploy/install.sh)")
        self.cv2 = cv2
        self.det = cv2.FaceDetectorYN.create(str(detector), "", (320, 320), min_detect_score, 0.3, 50)
        self.rec = cv2.FaceRecognizerSF.create(str(recognizer), "")

    def faces(self, img) -> list[Face]:
        h, w = img.shape[:2]
        self.det.setInputSize((w, h))
        _, found = self.det.detect(img)
        out = []
        for f in found if found is not None else []:
            x, y, fw, fh = (int(v) for v in f[:4])
            out.append(Face((x, y, fw, fh), float(min(fw, fh)), float(f[-1]), f))
        return out

    def embed(self, img, face: Face) -> np.ndarray:
        aligned = self.rec.alignCrop(img, face.raw)
        v = self.rec.feature(aligned).flatten().astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def crop_jpeg(self, img, face: Face) -> bytes:
        x, y, w, h = face.box
        m = int(0.25 * max(w, h))
        H, W = img.shape[:2]
        crop = img[max(0, y - m): min(H, y + h + m), max(0, x - m): min(W, x + w + m)]
        ok, buf = self.cv2.imencode(".jpg", crop)
        return buf.tobytes() if ok else b""


@dataclass
class Person:
    id: str
    name: str
    allowed: bool
    kind: str = "enrolled"  # enrolled | visitor
    embeddings: list[list[float]] = field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    seen: int = 0
    fingerprints: list[int] = field(default_factory=list)  # ids stored on the fingerprint sensor
    allowed_until: str = ""  # guest pass: allowed only until this time (YYYY-MM-DD HH:MM:SS)
    auto_unlock: bool = False  # their face alone unlocks the door, in any mode

    def is_allowed(self) -> bool:
        if not self.allowed:
            return False
        return not self.allowed_until or time.strftime("%Y-%m-%d %H:%M:%S") < self.allowed_until


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "person"


class FaceBank:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.path = self.folder / "bank.json"
        self._lock = threading.RLock()
        self.people: dict[str, Person] = {}
        self._matrix: np.ndarray | None = None
        self._owners: list[str] = []
        self._stamp: int | None = None
        self.refresh()

    def _disk_stamp(self) -> int | None:
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def refresh(self) -> bool:
        """Reload if another program (e.g. `phrasehome faces enroll` while the house is running)
        changed the bank on disk. Called before every match and every change, so the running
        house sees new faces at once and never writes over them."""
        with self._lock:
            stamp = self._disk_stamp()
            if stamp == self._stamp:
                return False
            people = {}
            if stamp is not None:
                try:
                    known = set(Person.__dataclass_fields__)
                    for p in json.loads(self.path.read_text()).get("people", []):
                        people[p["id"]] = Person(**{k: v for k, v in p.items() if k in known})
                except (OSError, ValueError):
                    return False  # caught mid-write; try again next time
            self.people, self._stamp, self._matrix = people, stamp, None
            return True

    def save(self) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"bank.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"people": [vars(p) for p in self.people.values()]}))
            tmp.replace(self.path)
            self._stamp = self._disk_stamp()
            self._matrix = None

    def _index(self) -> None:
        rows, owners = [], []
        for p in self.people.values():
            for e in p.embeddings:
                rows.append(e)
                owners.append(p.id)
        self._matrix = np.array(rows, dtype=np.float32) if rows else np.zeros((0, 1), np.float32)
        self._owners = owners

    def best_match(self, emb: np.ndarray, kind: str | None = None) -> tuple[Person | None, float]:
        """Closest person by cosine similarity (1.0 = identical). kind: only "enrolled" or
        only "visitor" people."""
        with self._lock:
            self.refresh()
            if self._matrix is None:
                self._index()
            if not self._owners:
                return None, 0.0
            sims = self._matrix @ emb
            if kind:
                mask = np.array([self.people[o].kind == kind for o in self._owners])
                if not mask.any():
                    return None, 0.0
                sims = np.where(mask, sims, -2.0)
            i = int(np.argmax(sims))
            return self.people[self._owners[i]], float(sims[i])

    def visitors_like(self, person: Person, threshold: float) -> list[Person]:
        """Visitor entries that are really this person (saved before they were enrolled, or on a
        bad frame)."""
        with self._lock:
            mine = np.array(person.embeddings, dtype=np.float32)
            if not len(mine):
                return []
            out = []
            for v in self.people.values():
                if v.kind == "visitor" and v.embeddings:
                    sims = np.array(v.embeddings, dtype=np.float32) @ mine.T
                    if float(np.median(sims.max(axis=1))) >= threshold:
                        out.append(v)
            return out

    def find(self, ref: str) -> Person:
        ref_l = ref.lower()
        for p in self.people.values():
            if p.id == ref_l or p.name.lower() == ref_l:
                return p
        raise KeyError(f"no one called '{ref}' in the face bank")

    def _new_id(self, base: str) -> str:
        i, pid = 1, base
        while pid in self.people:
            i += 1
            pid = f"{base}-{i}"
        return pid

    def enroll(self, name: str, embeddings: list[np.ndarray], allowed: bool = True) -> Person:
        with self._lock:
            self.refresh()
            try:
                p = self.find(name)
            except KeyError:
                p = Person(self._new_id(slug(name)), name, allowed, first_seen=_now())
                self.people[p.id] = p
            p.allowed = allowed
            p.kind = "enrolled"
            p.embeddings = (p.embeddings + [e.tolist() for e in embeddings])[-MAX_EMBEDDINGS:]
            self.save()
            return p

    def add_visitor(self, embedding: np.ndarray, photo: bytes = b"") -> Person:
        with self._lock:
            self.refresh()
            n = 1 + sum(1 for p in self.people.values() if p.kind == "visitor")
            p = Person(self._new_id(f"visitor-{n}"), f"visitor {n}", False, "visitor",
                       [embedding.tolist()], _now(), _now(), 1)
            self.people[p.id] = p
            self.save()
            if photo:
                self.save_photo(p.id, photo)
            return p

    def seen(self, person: Person, embedding: np.ndarray | None = None) -> None:
        with self._lock:
            self.refresh()
            person = self.people.get(person.id)
            if person is None:
                return  # deleted meanwhile
            person.seen += 1
            person.last_seen = _now()
            if embedding is not None and len(person.embeddings) < MAX_EMBEDDINGS:
                person.embeddings.append(embedding.tolist())
            self.save()

    def save_photo(self, pid: str, photo: bytes) -> Path:
        d = self.folder / pid
        d.mkdir(parents=True, exist_ok=True)
        f = d / time.strftime("%Y%m%d-%H%M%S.jpg")
        f.write_bytes(photo)
        return f

    def set_allowed(self, ref: str, allowed: bool, name: str | None = None,
                    hours: float | None = None) -> Person:
        with self._lock:
            self.refresh()
            p = self.find(ref)
            p.allowed = allowed
            p.allowed_until = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + hours * 3600)) \
                if (allowed and hours) else ""
            if name:
                p.name = name
            if allowed:
                p.kind = "enrolled"
            self.save()
            return p

    def set_auto_unlock(self, ref: str, on: bool = True) -> Person:
        with self._lock:
            self.refresh()
            p = self.find(ref)
            p.auto_unlock = on
            if on:
                p.allowed, p.allowed_until, p.kind = True, "", "enrolled"
            self.save()
            return p

    def rename(self, ref: str, name: str) -> Person:
        with self._lock:
            self.refresh()
            p = self.find(ref)
            p.name = name
            self.save()
            return p

    def by_fingerprint(self, fid: int) -> Person | None:
        return next((p for p in self.people.values() if fid in p.fingerprints), None)

    def add_fingerprint(self, ref: str, fid: int) -> Person:
        with self._lock:
            self.refresh()
            p = self.find(ref)
            if fid not in p.fingerprints:
                p.fingerprints.append(fid)
            self.save()
            return p

    def next_fingerprint_id(self) -> int:
        used = {f for p in self.people.values() for f in p.fingerprints}
        return next(i for i in range(1, 1000) if i not in used)

    def delete(self, ref: str) -> Person:
        with self._lock:
            self.refresh()
            p = self.people.pop(self.find(ref).id)
            self.save()
            return p
