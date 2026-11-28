"""Does the house hear itself on this computer? Plays a few lines through the speaker while the mic records,
then measures it.

    phrasehome voice echo-test

It reports how late the sound comes out of the speaker (wireless ones lag), how long the room echoes after,
and whether any of its own words got through to the part that listens for commands. If they did, it tells
you the `local_mic: {echo_tail: ...}` to put in config.yaml. Takes about 20 seconds; keep the room quiet.
"""

from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("phrasehome.echo")
LINES = ["Lights off. Enjoy sitting in the dark.", "Timer set for five minutes. Try not to burn it this time.",
         "House meeting booked for tomorrow at seven, in the living room."]
FRAME = 0.02


def _frames(chunks: list[tuple[float, bytes]], rate: int) -> tuple[np.ndarray, np.ndarray]:
    """(time of each 20 ms frame, its loudness) from (time recorded, audio) chunks."""
    times, levels = [], []
    n = int(rate * FRAME)
    for at, data in chunks:
        x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
        start = at - len(x) / rate  # the chunk ends when it's handed over
        for i in range(0, len(x) - n + 1, n):
            times.append(start + (i + n) / rate)
            levels.append(float(np.sqrt(np.mean(x[i:i + n] ** 2))))
    return np.array(times), np.array(levels)


def analyse(chunks: list[tuple[float, bytes]], windows: list[tuple[float, float]], rate: int = 16000) -> dict:
    """For each time the speaker played (start, end): how late its sound reached the mic, and how long after
    the end it could still be heard."""
    t, lv = _frames(chunks, rate)
    if not len(t):
        return {"silent": True, "lines": []}
    first = windows[0][0] if windows else t[-1]
    quiet = lv[t < first]
    base = float(np.median(quiet)) if len(quiet) else float(np.percentile(lv, 10))
    loud_on, loud_off = max(base * 4, 250.0), max(base * 2.5, 150.0)
    out = []
    for s, e in windows:
        after = (t >= s) & (t <= e + 3)
        on = t[after & (lv > loud_on)]
        latency = float(on[0] - s) if len(on) else None
        heard = t[(t >= e) & (t <= e + 3) & (lv > loud_off)]
        tail = float(heard[-1] - e) if len(heard) else 0.0
        out.append({"latency": latency, "tail": round(tail, 2)})
    return {"silent": float(lv.max(initial=0)) < 30, "room_noise": round(base), "lines": out}


def run(cfg, show=print) -> dict:
    import sounddevice as sd

    from . import audio_out
    from .dispatcher import Dispatcher

    from .listener import set_echo_tail

    rate = cfg.settings.sample_rate
    local = cfg.sources.get("local_mic") or {}
    tail = set_echo_tail(local, sd)  # as the real mic would
    show(f"Speaker: {sd.query_devices(kind='output')['name']}; it waits {tail:.1f} s after talking before listening")
    chunks: list[tuple[float, bytes]] = []

    def cb(indata, frames, time_info, status) -> None:
        chunks.append((time.monotonic(), bytes(indata)))

    home = Dispatcher(cfg)
    v = home.voice
    show("Making the test lines in the house's voice...")
    wavs = [v.synthesize(line, v.base) for line in LINES]
    if not all(wavs):
        raise SystemExit("The voice couldn't make the test lines: run phrasehome voice check")
    stream = sd.RawInputStream(samplerate=rate, blocksize=1600, dtype="int16", channels=1, callback=cb,
                               device=local.get("device", cfg.settings.device))
    windows = []
    try:
        stream.start()
        show("Listening to the room for a moment (keep it quiet)...")
        time.sleep(1.5)
        for line, wav in zip(LINES, wavs):
            show(f"  saying: {line}")
            audio_out.speaking_text = line
            n = len(audio_out._local_spoken)
            v.router._play_local(wav, 1.0)  # this computer's own speaker, marked as talking
            if len(audio_out._local_spoken) > n:
                s, e, _ = audio_out._local_spoken[-1]
                windows.append((s, e))
            time.sleep(3.0)
    finally:
        stream.stop()
        stream.close()
    r = analyse(chunks, windows, rate)
    r["leaked"] = _leaks(cfg, home, chunks, show)
    home.close()
    return r


def _leaks(cfg, home, chunks, show) -> list[str]:
    """Play the recording through the real listening code: did anything of its own voice get through?"""
    try:
        import vosk

        from . import listener
        from .listener import Engine

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(cfg.resolve_path(cfg.settings.model_path)))
    except Exception as e:
        show(f"(couldn't load the speech model to check what got through: {e})")
        return []
    heard: list[str] = []
    real = listener.handle_result
    listener.handle_result = lambda result, *a, **k: heard.append(result.get("text", ""))
    home.submit = lambda m: heard.append(m.heard) or True
    try:
        e = Engine(cfg, model, home)
        for at, data in chunks:
            e.feed("local", None, data, None, at)
        e.feed("local", None, b"\x00\x00" * 16000, None, time.monotonic())
    finally:
        listener.handle_result = real
    return [h for h in heard if h]


def report(r: dict, tail_now: float, show=print) -> int:
    if r.get("silent"):
        show("\nThe microphone only gave silence. On a Mac: System Settings > Privacy & Security > Microphone > "
             "allow Terminal (or Python), then quit and reopen Terminal.")
        return 1
    show(f"\nRoom noise: {r['room_noise']}")
    lat = [x["latency"] for x in r["lines"] if x["latency"] is not None]
    tails = [x["tail"] for x in r["lines"]]
    if not lat:
        show("The mic didn't hear the speaker at all: is the volume up, and is it playing through this computer?")
    else:
        show(f"Speaker delay: {max(lat):.2f} s" + ("  (a wireless speaker)" if max(lat) > 0.25 else ""))
    show(f"Echo after it stops: up to {max(tails):.2f} s (the house waits {tail_now:.1f} s before listening again)")
    need = round(max(tails + lat + [0]) + 0.3, 1)
    if r["leaked"]:
        show("Its own voice got through: " + "; ".join(repr(x) for x in r["leaked"]))
    else:
        show("None of its own words got through.")
    if need > tail_now or r["leaked"]:
        show(f"\nFix: in config.yaml, under sources: local_mic:, add  echo_tail: {max(need, tail_now + 0.5):.1f}\n"
             "Or turn the speaker down a little, or move the laptop's mic away from it.")
        return 1
    show("All good: it won't hear itself here.")
    return 0


def main(cfg) -> int:
    from . import audio_out

    r = run(cfg)
    return report(r, audio_out.ECHO_TAIL)
