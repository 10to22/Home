"""Send the command to Google Assistant as text — it runs exactly as if you'd said
"Hey Google, <command>", so anything your Google Home app controls works."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import google.oauth2.credentials
from gassist_text import TextAssistant

from .base import Backend

log = logging.getLogger("phrasehome.google")

SCOPE = "https://www.googleapis.com/auth/assistant-sdk-prototype"


class GoogleAssistantBackend(Backend):
    def __init__(self, name: str, cfg: dict[str, Any], config) -> None:
        super().__init__(name, cfg, config)
        path = config.resolve_path(cfg.get("credentials", "credentials.json"))
        if not path.exists():
            raise FileNotFoundError(
                f"Google credentials not found at {path}. Run: phrasehome auth-google"
            )
        info = json.loads(path.read_text())
        self.credentials = google.oauth2.credentials.Credentials(token=None, **info)
        self.language = cfg.get("language", "en-US")
        self._lock = threading.Lock()
        self._assistant: TextAssistant | None = None

    def _client(self) -> TextAssistant:
        if self._assistant is None:
            self._assistant = TextAssistant(self.credentials, self.language)
        return self._assistant

    def run(self, action: dict[str, Any], phrase: str) -> str:
        query = action.get("say") or phrase
        with self._lock:
            try:
                reply = self._client().assist(query)[0]
            except Exception:
                # Channel may have gone stale; rebuild once and retry.
                self.close()
                reply = self._client().assist(query)[0]
        log.info("Google Assistant <- %r -> %r", query, reply)
        return reply or ""

    def close(self) -> None:
        if self._assistant is not None:
            try:
                self._assistant.close()
            finally:
                self._assistant = None
