"""Phone notifications with the free ntfy app (https://ntfy.sh, or your own ntfy server).

backend:  phone: { type: ntfy, topic: my-house-8f3k2 }     # pick a long, unguessable topic
action:   { backend: phone, message: "Someone's at the {door} door", title: Door,
            priority: 4, tags: door, attach: "{photo}" }      # attach a local file (scan photo)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

from .base import Backend


class NtfyBackend(Backend):
    def run(self, action: dict[str, Any], phrase: str) -> str:
        server = str(action.get("server") or self.cfg.get("server", "https://ntfy.sh")).rstrip("/")
        topic = action.get("topic") or self.cfg.get("topic")
        if not topic:
            raise ValueError(f"backend '{self.name}': set a topic")
        headers = {}
        token = self.cfg.get("token") or os.environ.get(self.cfg.get("token_env", ""), "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for key, header in (("title", "Title"), ("priority", "Priority"), ("tags", "Tags"),
                            ("click", "Click")):
            if action.get(key) not in (None, ""):
                headers[header] = str(action[key]).encode("utf-8").decode("latin-1", "replace")
        message = str(action.get("message") or phrase or "phrasehome")
        attach = action.get("attach")
        if attach and Path(str(attach)).is_file():
            headers["Filename"] = Path(str(attach)).name
            headers["Message"] = message.encode("utf-8").decode("latin-1", "replace")
            r = requests.put(f"{server}/{topic}", data=Path(str(attach)).read_bytes(),
                             headers=headers, timeout=15)
        else:
            r = requests.post(f"{server}/{topic}", data=message.encode("utf-8"), headers=headers,
                              timeout=10)
        r.raise_for_status()
        return f"notified {topic}"
