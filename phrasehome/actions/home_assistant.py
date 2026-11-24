"""Home Assistant: call a service directly, or send text to its conversation agent."""

from __future__ import annotations

import os
from typing import Any

import requests

from .base import Backend, log


class HomeAssistantBackend(Backend):
    def __init__(self, name: str, cfg: dict[str, Any], config) -> None:
        super().__init__(name, cfg, config)
        self.url = cfg.get("url", "http://homeassistant.local:8123").rstrip("/")
        token = cfg.get("token") or os.environ.get(cfg.get("token_env", "HA_TOKEN"))
        if not token:
            raise ValueError(f"backend '{name}': set token_env (default HA_TOKEN) or token")
        self.headers = {"Authorization": f"Bearer {token}"}

    def run(self, action: dict[str, Any], phrase: str) -> str:
        if "service" in action:  # e.g. service: light.turn_on, data: {entity_id: light.kitchen}
            domain, service = action["service"].split(".", 1)
            r = requests.post(
                f"{self.url}/api/services/{domain}/{service}",
                headers=self.headers, json=action.get("data", {}), timeout=10,
            )
            r.raise_for_status()
            return f"called {action['service']}"
        text = action.get("say") or phrase
        r = requests.post(
            f"{self.url}/api/conversation/process",
            headers=self.headers, json={"text": text, "language": action.get("language", "en")},
            timeout=10,
        )
        r.raise_for_status()
        speech = r.json().get("response", {}).get("speech", {}).get("plain", {}).get("speech", "")
        log.info("Home Assistant <- %r -> %r", text, speech)
        return speech
