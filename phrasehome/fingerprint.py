"""Fingerprint second factor (R503 sensor on an ESP32: firmware/door_fingerprint).

Flow at the door:  face scan "allowed" -> "please place your finger" -> the same person's finger
within `window_seconds` -> fingerprint accepted -> on_accepted (default: unlock the door).
A finger without a matching face scan is refused (unless require_face: false). After
`max_failures` wrong fingers in a row, on_lockout runs (default: lock the door + say so).

fingerprint:
  - door: front
    host: phrasehome-finger-front.local
    window_seconds: 30
    require_face: true
    max_failures: 3
    on_accepted: [ { speak: fingerprint_accepted }, { door: "{door}", state: unlocked } ]
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from .faces import FaceBank

log = logging.getLogger("phrasehome.fingerprint")

DEFAULTS: dict[str, Any] = {
    "window_seconds": 30,
    "require_face": True,
    "max_failures": 3,
    "poll_seconds": 0.4,
    "on_accepted": [{"backend": "voice", "speak": "fingerprint_accepted"},
                    {"backend": "doors", "door": "{door}", "state": "unlocked"}],
    "on_rejected": [{"backend": "voice", "speak": "fingerprint_rejected"}],
    "on_lockout": [{"backend": "voice", "speak": "fingerprint_lockout"},
                   {"backend": "doors", "door": "{door}", "state": "locked"}],
}


class FingerprintDoor:
    def __init__(self, home, cfg: dict[str, Any]) -> None:
        self.home = home
        self.cfg = {**DEFAULTS, **cfg}
        self.door = str(self.cfg.get("door", "front")).lower()
        self.host = str(self.cfg.get("host", f"phrasehome-finger-{self.door}.local"))
        token = self.cfg.get("token") or os.environ.get(self.cfg.get("token_env", "PHRASEHOME_TOKEN"), "")
        self.headers = {"X-Token": token} if token else {}
        c = home.config
        self.bank = FaceBank(Path(c.resolve_path(c.data_dir)) / "faces")
        self.pending: tuple[str, float] | None = None  # (person id, expires) after a face match
        self.failures = 0
        self._seq: int | None = None
        self._running = False
        home.events.subscribe(self._on_event)

    def _on_event(self, ev: dict) -> None:
        if ev["kind"] == "scan.awaiting_fingerprint" and ev.get("door") == self.door:
            self.pending = (ev["person"], time.monotonic() + float(self.cfg["window_seconds"]))
            self.led("blue", "breathe")

    def led(self, color: str, mode: str = "on") -> None:
        try:
            requests.post(f"http://{self.host}/api/led", params={"color": color, "mode": mode},
                          headers=self.headers, timeout=2)
        except Exception:
            pass

    # ---------- the decision ----------
    def on_finger(self, matched: bool, fid: int | None) -> str:
        self.bank = FaceBank(self.bank.folder)  # pick up enrollments made by the CLI
        person = self.bank.by_fingerprint(fid) if matched and fid is not None else None
        pend = self.pending if self.pending and self.pending[1] > time.monotonic() else None
        if person and person.is_allowed() and (
                not self.cfg["require_face"] or (pend and pend[0] == person.id)):
            self.failures = 0
            self.pending = None
            self.led("purple", "flash")
            self.home.events.log("fingerprint.accepted", door=self.door, person=person.id,
                                 name=person.name)
            self._run("on_accepted", person.name)
            return "accepted"
        self.failures += 1
        reason = "no matching face scan" if person else "unknown finger"
        self.led("red", "flash")
        self.home.events.log("fingerprint.rejected", door=self.door, reason=reason,
                             name=person.name if person else "")
        if self.failures >= int(self.cfg["max_failures"]):
            self.failures = 0
            self.pending = None
            self.home.events.log("fingerprint.lockout", door=self.door)
            self._run("on_lockout", person.name if person else "")
            return "lockout"
        self._run("on_rejected", person.name if person else "")
        return "rejected"

    def _run(self, key: str, name: str) -> None:
        values = {"door": self.door, "name": name,
                  "here": self.home.config.doors.get(self.door, {}).get("room")}
        self.home.run_steps(self.cfg[key], values, f"fingerprint {key}")

    # ---------- talking to the sensor ----------
    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("fingerprint sensor for the %s door at %s", self.door, self.host)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                st = requests.get(f"http://{self.host}/api/status", timeout=2).json()
                seq = int(st.get("seq", 0))
                if self._seq is not None and seq != self._seq and st.get("result") in ("match", "nomatch"):
                    self.on_finger(st["result"] == "match", st.get("id"))
                self._seq = seq
            except Exception as e:
                log.debug("fingerprint sensor: %s", e)
                time.sleep(2)
            time.sleep(float(self.cfg["poll_seconds"]))

    def enroll(self, person_ref: str, timeout: float = 60) -> int:
        """Store a new finger on the sensor for this person (they press it twice)."""
        fid = self.bank.next_fingerprint_id()
        r = requests.post(f"http://{self.host}/api/enroll", params={"id": fid}, headers=self.headers,
                          timeout=5)
        r.raise_for_status()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = requests.get(f"http://{self.host}/api/status", timeout=3).json()
            if st.get("enroll") == "done":
                self.bank.add_fingerprint(person_ref, fid)
                return fid
            if st.get("enroll") == "failed":
                raise RuntimeError(st.get("error", "enrollment failed; try again"))
            time.sleep(0.5)
        raise TimeoutError("no finger placed in time")
