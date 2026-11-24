"""Voice control for ESP32 speaker modules.

actions:
    { backend: music, speaker: all, mode: group }          # "play everywhere"
    { backend: music, speaker: "{here}", mode: bluetooth } # this room's speaker on its own
    { backend: music, speaker: "{here}", volume: up }      # up / down / 0-100
"""

from __future__ import annotations

from typing import Any

from ..speakers import get_hub
from .base import Backend


class SpeakersBackend(Backend):
    def __init__(self, name: str, cfg: dict[str, Any], config) -> None:
        super().__init__(name, cfg, config)
        self.hub = get_hub(name, cfg, config)

    def run(self, action: dict[str, Any], phrase: str) -> str:
        speaker = str(action.get("speaker") or "all")
        parts = []
        if "mode" in action:
            parts.append(f"{action['mode']}: {', '.join(self.hub.set_mode(speaker, action['mode']))}")
        if "volume" in action:
            parts.append(f"volume {action['volume']}: {', '.join(self.hub.set_volume(speaker, action['volume']))}")
        if not parts:
            raise ValueError("speakers action needs `mode` and/or `volume`")
        return "; ".join(parts)
