"""Fire matched commands on a worker thread so listening never pauses."""

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
        self._last_fired: dict[str, float] = {}
        self._q: queue.Queue[Match | None] = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, m: Match) -> bool:
        now = time.monotonic()
        last = self._last_fired.get(m.command.name, -1e9)
        if now - last < self.config.settings.cooldown_seconds:
            log.debug("cooldown: skipping %s", m.command.name)
            return False
        self._last_fired[m.command.name] = now
        self._q.put(m)
        return True

    def _worker(self) -> None:
        while (m := self._q.get()) is not None:
            action = render(m.command.action, m.slots)
            if self.dry_run:
                log.info("DRY RUN %s -> %s", m.command.name, action)
                continue
            try:
                result = self.backends[action["backend"]].run(action, m.phrase)
                log.info("done: %s%s", m.command.name, f" ({result})" if result else "")
            except Exception as e:  # keep listening no matter what
                log.error("command %s failed: %s", m.command.name, e)

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=15)
        for b in self.backends.values():
            b.close()
