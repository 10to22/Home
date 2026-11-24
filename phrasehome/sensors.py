"""Polls ESP32 device nodes for their inputs (door contacts, motion, leak sensors, buttons,
soil moisture...) and turns changes into events that automations can react to. Also notices
modules that go offline (device nodes and mic satellites).

Events:
  sensor          {node, input, state: on|off}      digital input changed
  sensor          {node, input, state: pressed}     a button was pressed (never missed: counted)
  sensor          {node, input, value: 0-100}       analog input moved
  device.offline  {node}  /  device.online {node}
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

log = logging.getLogger("phrasehome.sensors")


class SensorWatcher:
    def __init__(self, home) -> None:
        self.home = home
        cfg = home.config.sensors if hasattr(home.config, "sensors") else {}
        self.poll = float(cfg.get("poll_seconds", 1.0))
        self.watchdog = float(cfg.get("watchdog_seconds", 60))
        self.offline_after = int(cfg.get("offline_after_failures", 3))
        self.analog_delta = float(cfg.get("analog_delta", 2))
        self.nodes: dict[str, str] = {}
        for b in home.config.backends.values():
            if b["type"] == "esp32":
                self.nodes.update({str(k).lower(): str(v) for k, v in (b.get("nodes") or {}).items()})
        self.values: dict[tuple[str, str], dict[str, Any]] = {}
        self._has_inputs: set[str] = set()
        self._next_poll: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self.offline: set[str] = set()
        self._running = False
        home.sensors = self

    def current(self, node: str | None, inp: str | None) -> dict[str, Any] | None:
        if not node or not inp:
            return None
        return self.values.get((str(node).lower(), str(inp).lower()))

    def start(self) -> None:
        if not self.nodes:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()
        log.info("watching %d device nodes for sensors", len(self.nodes))

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            now = time.monotonic()
            for node, host in self.nodes.items():
                if now >= self._next_poll.get(node, 0):
                    self.poll_node(node, host)
                    every = self.poll if node in self._has_inputs else self.watchdog
                    self._next_poll[node] = now + every
            self.check_satellites()
            time.sleep(min(self.poll, 1.0))

    def poll_node(self, node: str, host: str, status: dict | None = None) -> None:
        try:
            if status is None:
                r = requests.get(f"http://{host}/api/status", timeout=2)
                r.raise_for_status()
                status = r.json()
        except Exception:
            n = self._failures.get(node, 0) + 1
            self._failures[node] = n
            if n == self.offline_after and node not in self.offline:
                self.offline.add(node)
                self.home.events.log("device.offline", node=node)
            return
        self._failures[node] = 0
        if node in self.offline:
            self.offline.discard(node)
            self.home.events.log("device.online", node=node)
        inputs = status.get("inputs") or []
        if inputs:
            self._has_inputs.add(node)
        for inp in inputs:
            self._update(node, inp)

    def _update(self, node: str, inp: dict[str, Any]) -> None:
        name = str(inp.get("name", "")).lower()
        key = (node, name)
        prev = self.values.get(key)
        cur: dict[str, Any] = {"node": node, "input": name}
        if "value" in inp:
            cur["value"] = float(inp["value"])
        if "on" in inp:
            cur["state"] = "on" if inp["on"] else "off"
        if "count" in inp:
            cur["count"] = int(inp["count"])
        self.values[key] = cur
        if prev is None:
            return  # first reading: nothing "changed" yet
        if "state" in cur and cur["state"] != prev.get("state"):
            self.home.events.log("sensor", node=node, input=name, state=cur["state"])
        if "count" in cur and cur["count"] > prev.get("count", cur["count"]):
            self.home.events.log("sensor", node=node, input=name, state="pressed",
                                 presses=cur["count"] - prev["count"])
        if "value" in cur and abs(cur["value"] - prev.get("value", cur["value"])) >= self.analog_delta:
            self.home.events.log("sensor", node=node, input=name, value=cur["value"])

    def check_satellites(self) -> None:
        sats = self.home.satellites
        if not sats:
            return
        for room in list(sats.modules):
            name = f"mic {room}"
            alive = sats.online(room, within=60)
            if not alive and name not in self.offline:
                self.offline.add(name)
                self.home.events.log("device.offline", node=name)
            elif alive and name in self.offline:
                self.offline.discard(name)
                self.home.events.log("device.online", node=name)
