"""Household event log: door scans, visitors, commands, locks. Kept in memory for the AI
assistant and appended to data/events.jsonl so you can look back later."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


class EventLog:
    def __init__(self, path: Path | None = None, keep: int = 500) -> None:
        self.path = path
        self.recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._lock = threading.Lock()
        self._subscribers: list = []
        if path and path.exists():  # reload the tail so the assistant remembers recent history
            for line in path.read_text().splitlines()[-keep:]:
                try:
                    self.recent.append(json.loads(line))
                except ValueError:
                    pass

    def log(self, kind: str, /, **data: Any) -> dict[str, Any]:
        if "kind" in data:  # "kind" names the event itself; keep a field of that name as "type"
            data["type"] = data.pop("kind")
        ev = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, **data}
        with self._lock:
            self.recent.append(ev)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps(ev) + "\n")
        for fn in list(self._subscribers):
            try:
                fn(ev)
            except Exception:
                pass
        return ev

    def subscribe(self, fn) -> None:
        """Call fn(event) for every new event (keep it quick; hand slow work to a thread)."""
        self._subscribers.append(fn)

    def unsubscribe(self, fn) -> None:
        try:
            self._subscribers.remove(fn)
        except ValueError:
            pass

    def live(self, kind: str, /, **data: Any) -> None:
        """A moment-to-moment update for live screens (what was heard, what's being said): sent to
        subscribers like an event, but never saved."""
        ev = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "live": True, **data}
        for fn in list(self._subscribers):
            try:
                fn(ev)
            except Exception:
                pass

    def query(self, kind: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]:
        with self._lock:
            evs = list(self.recent)
        if kind:
            evs = [e for e in evs if e["kind"] == kind or e["kind"].startswith(kind + ".")]
        if since:
            evs = [e for e in evs if e["time"] >= since]
        return evs[-limit:]
