from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("phrasehome.actions")


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(value: Any, values: dict[str, str]) -> Any:
    """Fill {slot} placeholders in strings, recursively through dicts/lists."""
    if isinstance(value, str):
        return value.format_map(_Safe(values))
    if isinstance(value, dict):
        return {k: render(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, values) for v in value]
    return value


class Backend:
    """Base backend. type: log just logs the command (useful for testing)."""

    def __init__(self, name: str, cfg: dict[str, Any], config) -> None:
        self.name = name
        self.cfg = cfg
        self.config = config

    def run(self, action: dict[str, Any], phrase: str) -> str:
        """Execute. `action` already has slots rendered. `phrase` is what matched."""
        msg = action.get("say") or phrase
        log.info("[%s] would run: %s", self.name, msg)
        return msg

    def close(self) -> None:
        pass
