"""Listens for smoke and carbon-monoxide alarms through the room mics, using only the sound of
the beeps (no speech is involved). Works with the alarms you already have.

Smoke alarms use the standard "temporal-three" pattern (3 half-second beeps, pause); CO alarms
use "temporal-four" (4 short beeps, pause). Both beep at roughly 2.7-3.9 kHz. Two full patterns
in a row raise:   smoke.alarm {room, alarm: smoke | co}

It keeps listening in privacy mode (it only measures beep tones), but a module muted with its
button sends nothing.
"""

from __future__ import annotations

import time

import numpy as np

RATE = 16000
FRAME = 320  # 20 ms


class _Stream:
    def __init__(self) -> None:
        self.buf = np.zeros(0, dtype=np.int16)
        self.tone = False
        self.since = 0.0          # stream time the current on/off segment started
        self.t = 0.0              # stream time (seconds of audio seen)
        self.pulses: list[float] = []   # durations of recent beeps in the current group
        self.groups: list[tuple[float, str]] = []  # (time, kind) of completed patterns


class SmokeListener:
    def __init__(self, home=None, band=(2600, 4000), min_level: float = 300.0, ratio: float = 0.45,
                 cooldown: float = 120.0) -> None:
        self.home = home
        cfg = (home.config.extra.get("smoke") or {}) if home else {}
        self.band = tuple(cfg.get("band", band))
        self.min_level = float(cfg.get("min_level", min_level))
        self.ratio = float(cfg.get("ratio", ratio))
        self.cooldown = float(cfg.get("cooldown", cooldown))
        self.enabled = cfg.get("enabled", True)
        self.streams: dict[str, _Stream] = {}
        self._last_alarm: dict[str, float] = {}
        freqs = np.fft.rfftfreq(FRAME, 1 / RATE)
        self._band = (freqs >= self.band[0]) & (freqs <= self.band[1])
        self._all = freqs >= 200
        self._win = np.hanning(FRAME)
        if home is not None:
            home.smoke = self

    def is_tone(self, frame: np.ndarray) -> bool:
        x = frame.astype(np.float32) * self._win
        if np.sqrt(np.mean(x * x)) < self.min_level:
            return False
        spec = np.abs(np.fft.rfft(x)) ** 2
        total = spec[self._all].sum() + 1e-9
        return spec[self._band].sum() / total > self.ratio

    def feed(self, room: str, pcm: bytes) -> str | None:
        """Feed 16 kHz 16-bit mono audio. Returns "smoke" / "co" when an alarm is recognized."""
        if not self.enabled:
            return None
        s = self.streams.setdefault(room or "?", _Stream())
        s.buf = np.concatenate([s.buf, np.frombuffer(pcm, dtype=np.int16)])
        found = None
        while len(s.buf) >= FRAME:
            frame, s.buf = s.buf[:FRAME], s.buf[FRAME:]
            tone = self.is_tone(frame)
            s.t += FRAME / RATE
            if tone != s.tone:
                dur = s.t - s.since
                if s.tone:   # a beep just ended
                    s.pulses.append(dur)
                else:        # a silence just ended
                    if dur > 0.9 and s.pulses:  # long pause: the group before it is complete
                        found = self._group_done(room, s) or found
                    elif dur > 0.9:
                        s.pulses = []
                s.tone, s.since = tone, s.t
            elif not tone and s.pulses and s.t - s.since > 0.9:
                found = self._group_done(room, s) or found
        return found

    def _group_done(self, room: str, s: _Stream) -> str | None:
        p, s.pulses = s.pulses, []
        kind = None
        if len(p) == 3 and all(0.3 <= d <= 0.8 for d in p):
            kind = "smoke"
        elif len(p) == 4 and all(0.04 <= d <= 0.25 for d in p):
            kind = "co"
        if not kind:
            return None
        s.groups = [g for g in s.groups if s.t - g[0] < 20] + [(s.t, kind)]
        if sum(1 for g in s.groups if g[1] == kind) < 2:
            return None
        s.groups = []
        now = time.monotonic()
        if now - self._last_alarm.get(f"{room}:{kind}", -1e9) < self.cooldown:
            return None
        self._last_alarm[f"{room}:{kind}"] = now
        if self.home is not None:
            self.home.events.log("smoke.alarm", room=room, alarm=kind)
        return kind
