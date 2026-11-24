"""Who's home.

- Allowed door scans and accepted fingerprints mark that person home.
- "I'm leaving" / "I'm home" commands ({presence: away}) mark whoever's room it was said in.
- Optional: an instance's phone_ip is pinged every minute: answers -> home; silent for
  `away_after_minutes` (default 15) -> away. Phones sleep their Wi-Fi, so this is a hint, not proof.

Events: presence.arrived, presence.left, presence.first_home, presence.everyone_left
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time

log = logging.getLogger("phrasehome.presence")


class Presence:
    def __init__(self, home) -> None:
        self.home = home
        cfg = home.config.presence
        self.away_after = float(cfg.get("away_after_minutes", 15)) * 60
        self.every = float(cfg.get("ping_seconds", 60))
        self.phones = {i.id: i.phone_ip for i in home.instances.all.values() if i.phone_ip}
        self._last_reply: dict[str, float] = {}
        self._running = False
        home.events.subscribe(self._on_event)

    def person_for(self, name: str) -> str | None:
        n = (name or "").lower()
        for inst in self.home.instances.all.values():
            if not inst.main and inst.matches_person(n):
                return inst.id
        return n or None

    def _on_event(self, ev: dict) -> None:
        if (ev["kind"] == "scan" and ev.get("result") == "allowed") or ev["kind"] == "fingerprint.accepted":
            who = self.person_for(ev.get("name", ""))
            if who:
                threading.Thread(target=self.home.state.set_presence,
                                 args=(who, "home", ev["kind"]), daemon=True).start()

    def start(self) -> None:
        if not self.phones:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("presence: pinging %d phones", len(self.phones))

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            for who, ip in self.phones.items():
                self.check(who, ip)
            time.sleep(self.every)

    def check(self, who: str, ip: str, reachable: bool | None = None) -> None:
        if reachable is None:
            reachable = subprocess.run(["ping", "-c", "1", "-W", "2", ip],
                                       capture_output=True).returncode == 0
        now = time.monotonic()
        if reachable:
            self._last_reply[who] = now
            self.home.state.set_presence(who, "home", "phone")
        elif now - self._last_reply.get(who, now - self.away_after - 1) > self.away_after:
            self.home.state.set_presence(who, "away", "phone")
