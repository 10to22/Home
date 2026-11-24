"""Backends that carry out a matched command."""

from __future__ import annotations

import logging
from typing import Any

from .base import Backend, render
from .home_assistant import HomeAssistantBackend
from .shell import ShellBackend
from .webhook import WebhookBackend

log = logging.getLogger(__name__)

_TYPES: dict[str, type[Backend]] = {
    "home_assistant": HomeAssistantBackend,
    "webhook": WebhookBackend,
    "shell": ShellBackend,
    "log": Backend,
}


def build_backend(name: str, cfg: dict[str, Any], config) -> Backend:
    kind = cfg["type"]
    if kind == "google_assistant":
        from .google_assistant import GoogleAssistantBackend  # optional dependency

        return GoogleAssistantBackend(name, cfg, config)
    if kind not in _TYPES:
        raise ValueError(f"backend '{name}': unknown type '{kind}'")
    return _TYPES[kind](name, cfg, config)


__all__ = ["Backend", "build_backend", "render"]
