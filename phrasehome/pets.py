"""Pets: a feeding log ("did anyone feed the dog?"), the automatic feeder, the RFID pet door,
and the water bowl.

pets:
  luna:
    kind: dog
    meals_per_day: 2
    feeder: { node: kitchen, channel: feeder, for: 2 }     # optional automatic feeder
    door: phrasehome-petdoor.local                          # optional (firmware/pet_door)
    tag: luna                                               # optional Bluetooth collar tag

Saying "I fed the dog" (or the feeder running) logs a meal with who did it. The pet door reports
each time it opens for the pet's collar tag: petdoor.used {pet, direction}.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from . import clock

log = logging.getLogger("phrasehome.pets")


class Pets:
    def __init__(self, home) -> None:
        self.home = home
        self.pets = {str(n).lower(): dict(v or {}) for n, v in (home.config.extra.get("pets") or {}).items()}
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "pets.json"
        try:
            self.log: dict[str, list] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.log = {}
        self._door_seq: dict[str, int] = {}
        self._running = False
        home.pets = self

    def find(self, words: str = "") -> str:
        w = (words or "").lower()
        for name, cfg in self.pets.items():
            if name in w or str(cfg.get("kind", "")).lower() in w:
                return name
        if len(self.pets) == 1:
            return next(iter(self.pets))
        raise ValueError(f"which pet? ({', '.join(self.pets)})")

    def fed(self, pet: str, by: str = "", source: str = "voice") -> str:
        pet = self.find(pet)
        entry = {"time": clock.now().strftime("%Y-%m-%d %H:%M"), "by": by, "source": source}
        self.log.setdefault(pet, []).append(entry)
        self.log[pet] = self.log[pet][-200:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.log, indent=1))
        self.home.events.log("pet.fed", pet=pet, by=by)
        return f"Got it, {pet} has been fed."

    def feed(self, pet: str, by: str = "") -> str:
        pet = self.find(pet)
        f = self.pets[pet].get("feeder")
        if not f:
            raise ValueError(f"{pet} has no automatic feeder")
        f = dict(f)
        f.setdefault("backend", self.home.config.settings.default_backend)
        self.home.run_action({**f, "state": f.get("state", True)})
        return self.fed(pet, by, "feeder")

    def status(self, pet: str = "") -> str:
        pet = self.find(pet)
        today = clock.now().strftime("%Y-%m-%d")
        meals = [e for e in self.log.get(pet, []) if e["time"].startswith(today)]
        per_day = int(self.pets[pet].get("meals_per_day", 2))
        if not meals:
            return f"Nobody has fed {pet} today."
        last = meals[-1]
        when = clock.spoken_time(datetime.strptime(last["time"], "%Y-%m-%d %H:%M"))
        who = f" by {self._name(last['by'])}" if last.get("by") else ""
        text = f"Yes, {pet} was fed at {when}{who}."
        if len(meals) >= per_day:
            text += f" That's {len(meals)} meals today, so {pet} is done for the day."
        else:
            text += f" That's {len(meals)} of {per_day} meals today."
        return text

    def _name(self, pid: str) -> str:
        inst = self.home.instances.all.get(pid)
        return inst.name if inst else pid

    # ---------- pet door ----------
    def poll_doors(self) -> None:
        for pet, cfg in self.pets.items():
            host = cfg.get("door")
            if not host:
                continue
            try:
                st = requests.get(f"http://{host}/api/status", timeout=3).json()
            except Exception:
                continue
            seq = int(st.get("seq", 0))
            if pet in self._door_seq and seq != self._door_seq[pet]:
                self.home.events.log("petdoor.used", pet=st.get("pet") or pet,
                                     direction=st.get("direction", ""), allowed=st.get("allowed", True))
            self._door_seq[pet] = seq

    def start(self) -> None:
        if not any(c.get("door") for c in self.pets.values()):
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            self.poll_doors()
            time.sleep(1)
