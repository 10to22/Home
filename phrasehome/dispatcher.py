"""Fire matched commands on a worker thread so listening never pauses.

Also handles:
- cooldown: the same command in the same room won't refire for `cooldown_seconds`
- arbitration: if several mics hear the same command within `arbitration_seconds`,
  only the clearest one (score x confidence) fires
"""

from __future__ import annotations

import logging
import queue
import threading
import time

from .actions import Backend, build_backend, render
from .config import Config
from .matcher import Match

log = logging.getLogger("phrasehome")


class Dispatcher:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        self.dry_run = dry_run
        self.backends: dict[str, Backend] = {}
        if not dry_run:
            used = {c.action["backend"] for c in config.commands}
            for name in used:
                self.backends[name] = build_backend(name, config.backends[name], config)
        self._last_fired: dict[tuple[str, str | None], float] = {}
        self._pending: dict[str, tuple[float, Match]] = {}  # command name -> (deadline, best)
        self._lock = threading.Lock()
        self._q: queue.Queue[Match | None] = queue.Queue()
        self._stop = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, m: Match) -> bool:
        """Queue a match. Returns False if it was dropped by cooldown or lost arbitration."""
        s = self.config.settings
        now = time.monotonic()
        with self._lock:
            if now - self._last_fired.get((m.command.name, m.room), -1e9) < s.cooldown_seconds:
                log.debug("cooldown: skipping %s (%s)", m.command.name, m.room)
                return False
            if s.arbitration_seconds <= 0:
                self._last_fired[(m.command.name, m.room)] = now
                self._q.put(m)
                return True
            current = self._pending.get(m.command.name)
            if current is None:
                self._pending[m.command.name] = (now + s.arbitration_seconds, m)
                return True
            deadline, best = current
            if _quality(m) > _quality(best):
                self._pending[m.command.name] = (deadline, m)
                return True
            return False

    def _flush(self, force: bool = False) -> None:
        now = time.monotonic()
        with self._lock:
            for name, (deadline, m) in list(self._pending.items()):
                if force or now >= deadline:
                    del self._pending[name]
                    self._last_fired[(name, m.room)] = now
                    self._q.put(m)

    def _worker(self) -> None:
        while True:
            self._flush(force=self._stop)
            try:
                m = self._q.get(timeout=0.1)
            except queue.Empty:
                continue
            if m is None:
                return
            self._execute(m)

    def _execute(self, m: Match) -> None:
        values = dict(m.slots)
        if m.room:
            values.setdefault("here", m.room)
        action = render(m.command.action, values)
        if "{here}" in str(action):
            log.warning("%s needs {here} but the mic that heard it has no room set", m.command.name)
            _notify(m, "failed")
            return
        if m.room:
            log.info("firing %s from %s", m.command.name, m.room)
        _notify(m, "matched")
        if self.dry_run:
            log.info("DRY RUN %s -> %s", m.command.name, action)
            _notify(m, "done")
            return
        try:
            result = self.backends[action["backend"]].run(action, m.phrase)
            log.info("done: %s%s", m.command.name, f" ({result})" if result else "")
            _notify(m, "done")
        except Exception as e:  # keep listening no matter what
            log.error("command %s failed: %s", m.command.name, e)
            _notify(m, "failed")

    def close(self) -> None:
        self._stop = True
        self._flush(force=True)
        self._q.put(None)
        self._thread.join(timeout=15)
        for b in self.backends.values():
            b.close()


def _quality(m: Match) -> float:
    return m.score * m.confidence


def _notify(m: Match, status: str) -> None:
    if m.notify:
        try:
            m.notify(status)
        except Exception as e:
            log.debug("notify failed: %s", e)
