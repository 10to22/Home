"""Control phrasehome ESP32 device nodes (relays, lamps, LED strips) directly over Wi-Fi.

backend config:
    esp:
      type: esp32
      token_env: PHRASEHOME_TOKEN       # optional shared secret, same as TOKEN in the firmware
      nodes:                            # node name -> host
        kitchen: phrasehome-kitchen.local
        bedroom: 192.168.1.42

action:
    { backend: esp, node: kitchen, channel: lights, state: on }
    { backend: esp, node: "{here}", channel: lamp, level: 40 }     # 0-100 for dimmable channels
    { backend: esp, node: all, channel: lights, state: off }       # every node that has that channel
    { backend: esp, node: garden, channel: pump, state: on, for: 15 }  # on for 15 s, then off
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .base import Backend, log


def _state(v: Any) -> str | None:
    # YAML turns a bare `on`/`off` into True/False.
    if v is True:
        return "on"
    if v is False:
        return "off"
    if v is None:
        return None
    v = str(v).lower()
    if v not in ("on", "off", "toggle"):
        raise ValueError(f"state must be on, off or toggle (got {v!r})")
    return v


class Esp32Backend(Backend):
    def __init__(self, name: str, cfg: dict[str, Any], config) -> None:
        super().__init__(name, cfg, config)
        self.nodes = {str(k).lower(): str(v) for k, v in (cfg.get("nodes") or {}).items()}
        if not self.nodes:
            raise ValueError(f"backend '{name}': add at least one node under `nodes:`")
        self.token = cfg.get("token") or os.environ.get(cfg.get("token_env", "PHRASEHOME_TOKEN"), "")
        self.timeout = float(cfg.get("timeout", 4))

    def _hosts(self, node: str) -> list[tuple[str, str]]:
        node = node.lower()
        if node == "all":
            return list(self.nodes.items())
        if node not in self.nodes:
            raise ValueError(f"unknown node '{node}' (known: {', '.join(self.nodes)})")
        return [(node, self.nodes[node])]

    def get_state(self, node: str, channel: str) -> dict[str, Any]:
        """Read one channel from a node, e.g. {"name": "lock", "on": true}."""
        (_, host), = self._hosts(node)
        r = requests.get(f"http://{host}/api/status", timeout=self.timeout)
        r.raise_for_status()
        for ch in r.json().get("channels", []):
            if ch.get("name", "").lower() == channel.lower():
                return ch
        raise ValueError(f"node '{node}' has no channel '{channel}'")

    def run(self, action: dict[str, Any], phrase: str) -> str:
        node, channel = action.get("node"), action.get("channel")
        if not node or not channel:
            raise ValueError("esp32 action needs `node` and `channel`")
        params: dict[str, Any] = {"channel": channel}
        if (st := _state(action.get("state"))) is not None:
            params["state"] = st
        if action.get("level") is not None:
            params["level"] = int(action["level"])
        if action.get("for") not in (None, ""):
            params["for"] = float(action["for"])  # device switches back after this many seconds
        if len(params) == 1:
            raise ValueError("esp32 action needs `state` and/or `level`")

        headers = {"X-Token": self.token} if self.token else {}
        done, errors = [], []
        for name, host in self._hosts(str(node)):
            try:
                r = requests.post(f"http://{host}/api/set", params=params, headers=headers,
                                  timeout=self.timeout)
                if r.status_code == 404 and node == "all":
                    continue  # that node has no such channel
                r.raise_for_status()
                done.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")
        if errors and not done:
            raise RuntimeError("; ".join(errors))
        for e in errors:
            log.warning("esp32 %s", e)
        return f"{channel} {params.get('state', '')}{params.get('level', '')} on {', '.join(done)}"
