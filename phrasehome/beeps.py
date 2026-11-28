"""The beep listener: teach it your washer's, dryer's, microwave's or fridge-door beep once, and it tells
the right person when it goes off. No sensors: the room mics only measure tones (like the smoke-alarm
listener), so it works in privacy mode too and never hears words.

    phrasehome beeps learn washer --file washer.m4a --who finn    # a phone recording of the beep
    phrasehome beeps learn dryer --record 10 --room laundry       # or record it from this computer's mic
    phrasehome beeps list
    phrasehome beeps test --file washer.m4a                       # would it be recognised?

or in config.yaml, for what to say and who to tell:
beeps:
  washer: { say: "The washing's done", who: [finn] }      # who: names, "everyone", or leave out for whoever's home
  microwave: { say: "Microwave's done" }
  fridge: { say: "The fridge door's open", repeat_minutes: 2 }
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger("phrasehome.beeps")
RATE = 16000
HOP = 320          # 20 ms
WIN = 512


def _frames(x: np.ndarray):
    win = np.hanning(WIN)
    for i in range(0, len(x) - WIN, HOP):
        yield i, np.abs(np.fft.rfft(x[i:i + WIN] * win))


def _band(freq: float) -> tuple[int, int]:
    width = max(freq * 0.03, 45.0)
    lo, hi = (freq - width) * WIN / RATE, (freq + width) * WIN / RATE
    return int(np.floor(lo)), int(np.ceil(hi)) + 1


def learn(pcm: bytes) -> dict:
    """A beep's fingerprint from a recording: its pitch, how long each beep lasts, the gap, how many."""
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if len(x) < RATE // 4:
        raise ValueError("the recording's too short")
    specs = [s for _, s in _frames(x)]
    if not specs:
        raise ValueError("the recording's too short")
    S = np.array(specs)
    lo, hi = int(350 * WIN / RATE), int(6000 * WIN / RATE)
    loud = S.sum(axis=1) > np.percentile(S.sum(axis=1), 70)
    peak = lo + int(np.argmax(S[loud][:, lo:hi].mean(axis=0)))
    # refine the pitch between bins
    if 0 < peak < S.shape[1] - 1:  # between bins: a parabola through the peak and its neighbours
        a, b, c = S[loud][:, peak - 1:peak + 2].mean(axis=0)
        denom = a - 2 * b + c
        if denom < 0:
            peak = peak + 0.5 * (a - c) / denom
    freq = float(peak * RATE / WIN)
    on = _tone(S, freq)
    beeps = _segments(on)
    if not beeps:
        raise ValueError("couldn't find a clear beep in that recording")
    lens = [e - s for s, e in beeps]
    gaps = [b[0] - a[0] for a, b in zip(beeps, beeps[1:])]
    return {"freq": round(freq, 1), "beep_ms": int(np.median(lens) * HOP * 1000 / RATE),
            "gap_ms": int(np.median(gaps) * HOP * 1000 / RATE) if gaps else 0, "count": min(len(beeps), 6)}


def _tone(S: np.ndarray, freq: float) -> np.ndarray:
    """Per frame: is this tone clearly there (most of the frame's energy in its band, above the floor)?"""
    lo, hi = _band(freq)
    band = S[:, lo:hi].sum(axis=1)
    total = S[:, int(200 * WIN / RATE):].sum(axis=1) + 1e-9
    floor = np.percentile(band, 30) * 3 + 1e-3
    return (band / total > 0.25) & (band > floor)


def _segments(on: np.ndarray, min_frames: int = 2) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(list(on) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_frames:
                out.append((start, i))
            start = None
    return out


class _Room:
    def __init__(self) -> None:
        self.buf = np.zeros(0, dtype=np.float32)
        self.t = 0                       # frames seen
        self.state: dict[str, dict] = {}  # template -> {"on": start frame or None, "beeps": [start frames]}
        self.floor: dict[str, float] = {}


class Beeps:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("beeps") or {})
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "beeps.json"
        self.rooms: dict[str, _Room] = {}
        self._last: dict[tuple[str, str], float] = {}
        self._templates: dict | None = None

    # ---------- the fingerprints ----------
    def templates(self) -> dict[str, dict]:
        if self._templates is None:
            try:
                self._templates = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._templates = {}
        return self._templates

    def add(self, name: str, pcm: bytes, room: str | None = None, who: list[str] | None = None) -> dict:
        t = learn(pcm)
        if room:
            t["room"] = room.lower()
        if who:
            t["who"] = list(who)
        self.templates()[name.lower()] = t
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._templates, indent=1))
        return t

    def remove(self, name: str) -> bool:
        gone = self.templates().pop(name.lower(), None) is not None
        self.path.write_text(json.dumps(self._templates, indent=1))
        return gone

    # ---------- listening ----------
    def feed(self, room: str, pcm: bytes) -> list[str]:
        """Live audio from a room mic. Returns the names of beeps recognised just now."""
        temps = self.templates()
        if not temps or not pcm:
            return []
        r = self.rooms.setdefault(room, _Room())
        r.buf = np.concatenate([r.buf, np.frombuffer(pcm, dtype=np.int16).astype(np.float32)])
        found = []
        win = np.hanning(WIN)
        while len(r.buf) >= WIN:
            spec = np.abs(np.fft.rfft(r.buf[:WIN] * win))
            r.buf = r.buf[HOP:]
            r.t += 1
            total = spec[int(200 * WIN / RATE):].sum() + 1e-9
            for name, t in temps.items():
                if t.get("room") and t["room"] != room:
                    continue
                lo, hi = _band(t["freq"])
                band = float(spec[lo:hi].sum())
                fl = r.floor.get(name, band)
                on = band / total > 0.25 and band > fl * 3 + 1e-3
                if not on:  # follow the background in this band
                    r.floor[name] = fl + (band - fl) * 0.05
                st = r.state.setdefault(name, {"on": None, "beeps": []})
                if on and st["on"] is None:
                    st["on"] = r.t
                elif not on and st["on"] is not None:
                    length = r.t - st["on"]
                    want = max(2, t["beep_ms"] * RATE / 1000 / HOP)
                    if 0.5 * want <= length <= 1.8 * want + 2:
                        st["beeps"].append(st["on"])
                    st["on"] = None
                    if self._pattern(st["beeps"], t):
                        st["beeps"].clear()
                        if self._fire(name, room, t):
                            found.append(name)
        return found

    @staticmethod
    def _pattern(beeps: list[int], t: dict) -> bool:
        need = max(1, min(int(t.get("count", 1)), 3))
        gap = t.get("gap_ms", 0) * RATE / 1000 / HOP
        if gap:
            # keep only the beeps that fit the rhythm, counted back from the newest
            chain = [beeps[-1]] if beeps else []
            for b in reversed(beeps[:-1]):
                d = chain[-1] - b
                if 0.6 * gap <= d <= 1.5 * gap:
                    chain.append(b)
                elif d > 1.5 * gap:
                    break
            return len(chain) >= need
        return len(beeps) >= need

    def _fire(self, name: str, room: str, t: dict) -> bool:
        cfg = dict(self.cfg.get(name) or {})
        cool = float(cfg.get("repeat_minutes", 10)) * 60
        key = (name, room)
        if time.monotonic() - self._last.get(key, -1e9) < cool:
            return False
        self._last[key] = time.monotonic()
        h = self.home
        say = str(cfg.get("say") or f"The {name}'s done.")
        h.events.log("appliance.beep", name=name, room=room)
        who = cfg.get("who", t.get("who"))
        insts = h.instances
        if who in ("everyone", "all"):
            people = [i for i in insts.all.values() if not i.main]
        elif who:
            names = [who] if isinstance(who, str) else list(who)
            people = [i for i in insts.all.values() if any(i.matches_person(str(n)) for n in names)]
        else:
            here = set(h.state.home_people())
            people = [i for i in insts.all.values() if not i.main and (i.id in here or not here)]
        for p in people:
            h.notify_person(p, say, title=name.title())
        rooms = [p.rooms[0] for p in people if p.rooms and (p.id in h.state.home_people())] or insts.main.rooms or None
        h.voice.speak(say, rooms=rooms)
        return True


def beeps_for(home) -> Beeps:
    b = home.__dict__.get("_beeps")
    if b is None:
        b = home.__dict__["_beeps"] = Beeps(home)
    return b


def main(args) -> int:
    from .config import load_config
    from .dispatcher import Dispatcher
    from .hearing_train import to_pcm

    cfg = load_config(args.config)
    home = Dispatcher(cfg, dry_run=True)
    b = beeps_for(home)
    try:
        if args.action == "list":
            for n, t in b.templates().items():
                print(f"{n}: {t['freq']:.0f} Hz, {t['count']} beeps of {t['beep_ms']} ms every {t['gap_ms']} ms"
                      + (f", {t['room']}" if t.get("room") else ""))
            if not b.templates():
                print("No beeps learned yet: phrasehome beeps learn washer --file washer.m4a")
            return 0
        if args.action == "forget":
            print("forgotten" if b.remove(args.name) else "no such beep")
            return 0
        if args.file:
            pcm = to_pcm(args.file)
        else:
            import sounddevice as sd

            print(f"Recording {args.record:.0f} s: make it beep now.")
            pcm = sd.rec(int(args.record * RATE), samplerate=RATE, channels=1, dtype="int16")
            sd.wait()
            pcm = pcm.tobytes()
        if args.action == "learn":
            t = b.add(args.name, pcm, args.room, args.who)
            print(f"Learned {args.name}: {t['freq']:.0f} Hz, {t['count']} beeps of {t['beep_ms']} ms, every {t['gap_ms']} ms")
        else:
            found = b.feed(args.room or "test", pcm + b"\x00\x00" * RATE)
            print("Recognised: " + ", ".join(found) if found else "Not recognised as any learned beep.")
        return 0
    finally:
        home.close()
