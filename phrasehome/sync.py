"""Share what the house has learned between computers (your Mac while testing, the Pi in the house).

    phrasehome sync http://house.local:8080             # merge both ways, once
    sync: { peer: http://house.local:8080, every_minutes: 30 }   # in config.yaml: keep them in step

What's shared: taught and learned wordings (aliases), accent fixes, train-hearing's proven fixes, and the
"was that for the house?" examples. Voiceprints too, when both sides use the same voice ID model.
Nothing is deleted by a merge: wordings are joined (the newer one wins a clash), fix counts take the
higher number.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.sync")

FILES = ["aliases.json", "accent.json", "accent_trained.json", "intent_examples.json", "voiceprints.json"]


def _data(config) -> Path:
    return Path(config.resolve_path(config.data_dir))


def bundle(config) -> dict[str, Any]:
    out = {}
    for f in FILES:
        try:
            out[f] = json.loads((_data(config) / f).read_text())
        except (OSError, ValueError):
            continue
    return out


def merge_file(name: str, mine: Any, theirs: Any) -> Any:
    if mine is None:
        return theirs
    if theirs is None:
        return mine
    if name == "aliases.json" and isinstance(mine, list) and isinstance(theirs, list):
        by: dict[str, dict] = {}
        for a in mine + theirs:
            k = a.get("heard")
            if k and (k not in by or str(a.get("added", "")) > str(by[k].get("added", ""))):
                by[k] = a
        return list(by.values())
    if name == "accent.json" and isinstance(mine, dict) and isinstance(theirs, dict):
        return {k: max(int(mine.get(k, 0)), int(theirs.get(k, 0))) for k in set(mine) | set(theirs)}
    if isinstance(mine, dict) and isinstance(theirs, dict):
        return {**theirs, **mine}  # this computer's own wins a clash
    if isinstance(mine, list) and isinstance(theirs, list):
        seen, out = set(), []
        for x in mine + theirs:
            k = json.dumps(x, sort_keys=True, default=str)
            if k not in seen:
                seen.add(k)
                out.append(x)
        return out
    return mine


def merge_in(config, theirs: dict[str, Any], home=None) -> dict[str, int]:
    """Merge another computer's bundle into this one's data. Returns how many entries each file now has."""
    mine = bundle(config)
    counts = {}
    for name in FILES:
        if name not in theirs:
            continue
        if name == "voiceprints.json" and mine.get(name) and not _same_voice_model(mine[name], theirs[name]):
            log.info("voiceprints made with a different voice model: not shared")
            continue
        merged = merge_file(name, mine.get(name), theirs[name])
        path = _data(config) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, indent=1))
        tmp.replace(path)
        counts[name] = len(merged) if hasattr(merged, "__len__") else 1
    engine = getattr(home, "engine", None) if home is not None else None
    if engine is not None:
        try:
            engine.reload()  # new wordings and fixes take effect now
        except Exception as e:
            log.debug("reload after sync: %s", e)
    return counts


def _same_voice_model(a: Any, b: Any) -> bool:
    ma = a.get("model") if isinstance(a, dict) else None
    mb = b.get("model") if isinstance(b, dict) else None
    return ma == mb


def sync_with(config, peer: str, token: str = "", home=None) -> dict[str, Any]:
    """Pull the peer's learning, merge it here, push the merged result back."""
    import requests

    url = peer.rstrip("/")
    h = {"X-Token": token} if token else {}
    theirs = requests.get(url + "/api/learned", headers=h, timeout=20)
    theirs.raise_for_status()
    here = merge_in(config, theirs.json(), home)
    r = requests.post(url + "/api/learned", headers=h, json=bundle(config), timeout=30)
    r.raise_for_status()
    return {"here": here, "there": r.json().get("merged", {})}


class Syncer:
    """Keeps two computers' learning in step (sync: {peer, every_minutes, token})."""

    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("sync") or {}
        self.peer = str(c.get("peer") or "")
        self.every = float(c.get("every_minutes", 30)) * 60
        self.token = str(c.get("token") or home.config.web.get("token") or "")
        self._running = False
        self.last: dict[str, Any] = {}

    def start(self) -> None:
        if not self.peer:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.last = {"time": time.strftime("%Y-%m-%d %H:%M"), **sync_with(self.home.config, self.peer,
                                                                                  self.token, self.home)}
                log.info("synced learning with %s", self.peer)
            except Exception as e:
                self.last = {"time": time.strftime("%Y-%m-%d %H:%M"), "error": str(e)[:200]}
                log.info("couldn't sync with %s: %s", self.peer, e)
            for _ in range(int(self.every)):
                if not self._running:
                    return
                time.sleep(1)


def main(args) -> int:
    from .config import load_config

    cfg = load_config(args.config)
    peer = args.peer or (cfg.extra.get("sync") or {}).get("peer")
    if not peer:
        print("Which computer? phrasehome sync http://house.local:8080")
        return 2
    out = sync_with(cfg, peer, args.token or str(cfg.web.get("token") or ""))
    print(f"Synced with {peer}. Here now: {out['here']}. There now: {out['there']}.")
    return 0
