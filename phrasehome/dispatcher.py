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
from pathlib import Path
from typing import Any

from .actions import Backend, build_backend, render
from .config import Config
from .events import EventLog
from .matcher import Match

log = logging.getLogger("phrasehome")


class Dispatcher:
    def __init__(self, config: Config, dry_run: bool = False) -> None:
        self.config = config
        config.home = self  # lets backends, routines and the scanner reach shared services
        self.dry_run = dry_run
        data = Path(config.resolve_path(config.data_dir))
        self.events = EventLog(data / "events.jsonl")
        self.backends: dict[str, Backend] = {}
        self._backend_errors: dict[str, Exception] = {}
        self._backend_lock = threading.Lock()
        self._voice = self._doors = None
        if not dry_run:
            for name in config.backends:  # build up front so config problems show at startup
                try:
                    self.backend(name)
                except Exception as e:
                    log.error("backend %s unavailable: %s", name, e)
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

    # ---------- shared services ----------
    @property
    def voice(self):
        if self._voice is None:
            from .voice import Voice

            self._voice = Voice(self.config, dry_run=self.dry_run)
        return self._voice

    @property
    def doors(self):
        if self._doors is None:
            from .doors import Doors

            self._doors = Doors(self)
        return self._doors

    def backend(self, name: str) -> Backend:
        with self._backend_lock:
            if name in self.backends:
                return self.backends[name]
            if name in self._backend_errors:
                raise RuntimeError(f"backend {name} unavailable: {self._backend_errors[name]}")
            from .actions.builtin import DoorsBackend, RoutineBackend, VoiceBackend

            builtin = {"voice": VoiceBackend, "doors": DoorsBackend, "routine": RoutineBackend}
            try:
                if name in builtin:
                    b = builtin[name](name, {}, self.config)
                elif name in self.config.backends:
                    b = build_backend(name, self.config.backends[name], self.config)
                else:
                    raise ValueError(f"no backend named '{name}'")
            except Exception as e:
                self._backend_errors[name] = e
                raise
            self.backends[name] = b
            return b

    def render(self, action: dict[str, Any], values: dict[str, Any] | None = None) -> dict:
        action = render(action, {k: str(v) for k, v in (values or {}).items()})
        if "{here}" in str(action):
            raise ValueError("this needs {here}, but the mic that heard it has no room set")
        return action

    def run_action(self, action: dict[str, Any], values: dict[str, Any] | None = None,
                   phrase: str = "") -> str:
        """Run one action now, on the calling thread. Used by commands, routines, doors,
        the door scanner and the AI assistant."""
        action = self.render(action, values)
        name = action["backend"]
        if name in ("routine", "doors", "voice"):
            # built-ins also get the context ({name}, {door}, {room}...) for their own templates
            action["_context"] = {k: v for k, v in (values or {}).items()}
        elif self.dry_run:
            log.info("DRY RUN %s", action)
            return "dry run"
        return self.backend(name).run(action, phrase)

    def _execute(self, m: Match) -> None:
        values = dict(m.slots)
        if m.room:
            values.setdefault("here", m.room)
        if m.room:
            log.info("firing %s from %s", m.command.name, m.room)
        _notify(m, "matched")
        try:
            result = self.run_action(m.command.action, values, m.phrase)
            log.info("done: %s%s", m.command.name, f" ({result})" if result else "")
            self.events.log("command", name=m.command.name, room=m.room, heard=m.heard)
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
