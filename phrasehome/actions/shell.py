"""Run a local program. `command` must be a list (no shell), e.g. ["/usr/bin/aplay", "ding.wav"]."""

from __future__ import annotations

import subprocess
from typing import Any

from .base import Backend


class ShellBackend(Backend):
    def run(self, action: dict[str, Any], phrase: str) -> str:
        cmd = action.get("command")
        if not isinstance(cmd, list) or not cmd:
            raise ValueError(f"backend '{self.name}': action.command must be a non-empty list")
        out = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            raise RuntimeError(f"{cmd[0]} exited {out.returncode}: {out.stderr.strip()}")
        return out.stdout.strip()
