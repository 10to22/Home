"""Package tracking (17TRACK: one service for UPS, FedEx, USPS, DHL, Amazon and ~2,000 carriers).

packages:
  api_key_env: TRACK17_KEY        # free key from https://api.17track.net
  refresh_minutes: 30

Add a tracking number from the dashboard, `phrasehome package add <number> --name "new shoes"`,
or by asking the AI. Events: package.status {name, status, description}, plus
package.out_for_delivery and package.carrier_delivered (pair nicely with the porch camera).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import clock

log = logging.getLogger("phrasehome.packages")

API = "https://api.17track.net/track/v2.2"
STATUS_WORDS = {"NotFound": "not found yet", "InfoReceived": "label created", "InTransit": "in transit",
                "Expired": "tracking expired", "AvailableForPickup": "ready for pickup",
                "OutForDelivery": "out for delivery", "DeliveryFailure": "delivery failed",
                "Delivered": "delivered", "Exception": "has a problem"}


def _find(obj: Any, key: str) -> Any:
    """Depth-first search for a key, so small API shape changes don't break us."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def parse_info(item: dict) -> dict[str, str]:
    latest = _find(item, "latest_status") or {}
    event = _find(item, "latest_event") or {}
    eta = _find(item, "estimated_delivery_date") or {}
    return {"status": str(latest.get("status") or "NotFound"),
            "description": str(event.get("description") or ""),
            "location": str(event.get("location") or ""),
            "eta": str((eta.get("from") or eta.get("to") or "") if isinstance(eta, dict) else eta)[:10]}


class Packages:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("packages") or {})
        self.key = self.cfg.get("api_key") or os.environ.get(self.cfg.get("api_key_env", "TRACK17_KEY"), "")
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "packages.json"
        try:
            self.items: list[dict[str, Any]] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.items = []
        self._lock = threading.Lock()
        self._running = False
        home.packages = self

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.items, indent=1))

    def _post(self, path: str, body: list) -> dict:
        if not self.key:
            raise RuntimeError("set a 17TRACK API key (packages.api_key_env, default TRACK17_KEY)")
        r = requests.post(f"{API}/{path}", json=body, headers={"17token": self.key}, timeout=20)
        r.raise_for_status()
        return r.json()

    # ---------- managing ----------
    def add(self, number: str, name: str = "", owner: str = "") -> dict:
        number = "".join(ch for ch in number if ch.isalnum()).upper()
        if len(number) < 8:
            raise ValueError("that doesn't look like a tracking number")
        with self._lock:
            for it in self.items:
                if it["number"] == number:
                    return it
            item = {"number": number, "name": name or f"package {number[-4:]}", "owner": owner,
                    "status": "NotFound", "description": "", "eta": "", "added": clock.now().strftime("%Y-%m-%d")}
            self.items.append(item)
            self._save()
        try:
            self._post("register", [{"number": number}])
        except Exception as e:
            log.warning("couldn't register %s with 17TRACK: %s", number, e)
        return item

    def remove(self, ref: str) -> int:
        ref = ref.lower()
        with self._lock:
            keep = [i for i in self.items if ref not in (i["number"].lower(), i["name"].lower())
                    and ref not in i["name"].lower()]
            n = len(self.items) - len(keep)
            self.items = keep
            self._save()
        return n

    def refresh(self, infos: list[dict] | None = None) -> None:
        """Fetch the latest status (or apply `infos` in tests) and raise events on changes."""
        with self._lock:
            active = [i for i in self.items if i["status"] != "Delivered"]
        if not active:
            return
        if infos is None:
            data = self._post("gettrackinfo", [{"number": i["number"]} for i in active])
            infos = _find(data, "accepted") or []
        by_num = {str(_find(x, "number") or "").upper(): x for x in infos}
        for it in active:
            x = by_num.get(it["number"])
            if x is None:
                continue
            new = parse_info(x)
            if new["status"] != it["status"]:
                self.home.events.log("package.status", name=it["name"], number=it["number"],
                                     status=new["status"], words=STATUS_WORDS.get(new["status"], new["status"]),
                                     description=new["description"])
                if new["status"] == "OutForDelivery":
                    self.home.events.log("package.out_for_delivery", name=it["name"])
                if new["status"] == "Delivered":
                    self.home.events.log("package.carrier_delivered", name=it["name"])
                    it["delivered"] = clock.now().strftime("%Y-%m-%d")
            it.update(new)
        with self._lock:
            today = clock.now().strftime("%Y-%m-%d")
            # forget delivered packages after two days
            self.items = [i for i in self.items if not (i.get("delivered") and i["delivered"] < _days_ago(2))
                          or i.get("delivered") == today]
            self._save()

    def report(self) -> str:
        if not self.items:
            return "You don't have any packages on the way."
        parts = []
        for i in self.items:
            s = f"{i['name']} is {STATUS_WORDS.get(i['status'], i['status'])}"
            if i.get("eta") and i["status"] not in ("Delivered",):
                s += f", expected {i['eta']}"
            parts.append(s)
        return ". ".join(parts) + "."

    def start(self) -> None:
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                if self.key:
                    self.refresh()
            except Exception as e:
                log.warning("package tracking: %s", e)
            time.sleep(float(self.cfg.get("refresh_minutes", 30)) * 60)


def _days_ago(n: int) -> str:
    import datetime as _dt

    return (clock.now() - _dt.timedelta(days=n)).strftime("%Y-%m-%d")
