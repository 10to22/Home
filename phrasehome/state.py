"""Household state that survives restarts: mode (home/away/night/vacation/guest), who's home,
and privacy (mics paused)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

MODES = ["home", "away", "night", "vacation", "guest"]


class HomeState:
    def __init__(self, path: Path, events) -> None:
        self.path = path
        self.events = events
        self._lock = threading.Lock()
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        self.mode: str = data.get("mode", "home")
        self.presence: dict[str, dict[str, Any]] = data.get("presence", {})
        self.privacy_until: dict[str, float] = {}  # room or "*" -> time.time() it ends

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": self.mode, "presence": self.presence}))
        tmp.replace(self.path)

    # ---------- mode ----------
    def set_mode(self, mode: str, reason: str = "") -> str:
        mode = str(mode).lower()
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        with self._lock:
            old, self.mode = self.mode, mode
            self._save()
        if old != mode:
            self.events.log("mode", mode=mode, previous=old, reason=reason)
        return f"mode {mode}"

    # ---------- presence ----------
    def set_presence(self, person: str, state: str, source: str = "") -> bool:
        """Returns True if it changed."""
        person, state = person.lower(), state.lower()
        if state not in ("home", "away"):
            raise ValueError("presence must be home or away")
        with self._lock:
            before_home = self.home_people()
            cur = self.presence.get(person, {}).get("state")
            if cur == state:
                self.presence[person]["seen"] = time.time()
                return False
            self.presence[person] = {"state": state, "since": time.strftime("%Y-%m-%d %H:%M:%S"),
                                     "seen": time.time(), "source": source}
            self._save()
            after_home = self.home_people()
        self.events.log("presence." + ("arrived" if state == "home" else "left"),
                        person=person, source=source)
        if before_home and not after_home:
            self.events.log("presence.everyone_left", person=person)
        if not before_home and after_home:
            self.events.log("presence.first_home", person=person)
        return True

    def home_people(self) -> list[str]:
        return sorted(p for p, v in self.presence.items() if v.get("state") == "home")

    # ---------- privacy (mics paused) ----------
    def set_privacy(self, on: bool, minutes: float | None, room: str | None) -> str:
        key = room or "*"
        if on:
            self.privacy_until[key] = time.time() + (minutes * 60 if minutes else 10 * 365 * 86400)
            self.events.log("privacy.on", room=room, minutes=minutes)
            return f"listening paused{' in ' + room if room else ''}"
        self.privacy_until.pop(key, None)
        if key == "*":
            self.privacy_until.clear()
        self.events.log("privacy.off", room=room)
        return "listening again"

    def is_private(self, room: str | None) -> bool:
        t = time.time()
        for key in ("*", room):
            until = self.privacy_until.get(key) if key else None
            if until and until > t:
                return True
        return False
