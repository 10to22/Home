"""Generic HTTP request (IFTTT, Node-RED, SmartThings, your own server...)."""

from __future__ import annotations

from typing import Any

import requests

from .base import Backend


class WebhookBackend(Backend):
    def run(self, action: dict[str, Any], phrase: str) -> str:
        url = action.get("url") or self.cfg.get("url")
        if not url:
            raise ValueError(f"backend '{self.name}': no url")
        method = (action.get("method") or self.cfg.get("method") or "POST").upper()
        headers = {**self.cfg.get("headers", {}), **action.get("headers", {})}
        body = action.get("json", {"phrase": phrase, **({"say": action["say"]} if "say" in action else {})})
        r = requests.request(method, url, headers=headers, json=body, timeout=10)
        r.raise_for_status()
        return f"{method} {url} -> {r.status_code}"
