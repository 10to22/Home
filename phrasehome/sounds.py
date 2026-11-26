"""Built-in sounds, generated on first use (no files to download): chime, doorbell, alarm,
timer and reminder tones. Record or drop your own voice_bank/<name>.wav to replace any of them."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

RATE = 22050

# name -> list of (frequency Hz or 0 for silence, seconds)
SOUNDS: dict[str, list[tuple[float, float]]] = {
    "chime": [(880, 0.18), (0, 0.04), (1318.5, 0.35)],
    "doorbell": [(659.3, 0.5), (0, 0.05), (523.3, 0.8)],
    "alarm_sound": [(988, 0.15), (0, 0.08)] * 4 + [(0, 0.3)],
    "timer_sound": [(1046.5, 0.12), (0, 0.08)] * 3,
    "reminder_sound": [(784, 0.15), (0, 0.03), (1046.5, 0.25)],
    "siren": [(700 + 300 * (i % 2), 0.25) for i in range(8)],
    "listening_sound": [(1318.5, 0.08)],
    "go_on_sound": [(987.8, 0.07), (0, 0.03), (1318.5, 0.11)],  # "go on?": half-heard the name
}


def make(name: str, folder: Path) -> Path | None:
    if name not in SOUNDS:
        return None
    out = Path(folder) / f"sound-{name}.wav"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for freq, dur in SOUNDS[name]:
        n = int(RATE * dur)
        for i in range(n):
            if freq <= 0:
                v = 0.0
            else:
                env = min(1.0, i / (0.01 * RATE), (n - i) / (0.05 * RATE))  # soft attack/decay
                v = 0.45 * env * (math.sin(2 * math.pi * freq * i / RATE)
                                  + 0.25 * math.sin(4 * math.pi * freq * i / RATE))
            frames += struct.pack("<h", int(max(-1, min(1, v)) * 32767))
    tmp = out.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    tmp.replace(out)
    return out
