"""Backups of everything the house has learned: house points, what it knows about people, reminders, taught
wordings and hearing fixes, face bank and voiceprints, recorded voice lines, config and secrets.

    phrasehome backup                    # one now, into backups/ (next to config.yaml)
    phrasehome backup --to /Volumes/USB  # somewhere else
    phrasehome backup --list
    phrasehome backup --restore backups/phrasehome-backup-20261128-0330.tar.gz

    backup: { auto: true, at: "03:30", keep: 14, to: backups }   # in config.yaml (this is the default)

Restoring never deletes anything: the current data folder is moved aside (data.before-restore-<time>) and the
backup's config is saved as config.restored.yaml for you to compare, not over your config.yaml.
Backups hold face data, voices and secrets (.env): they're private to your user. Copy one off the computer now
and then (a USB stick, or a private cloud folder) so a dead laptop or SD card doesn't take the house's memory.
"""

from __future__ import annotations

import logging
import tarfile
import threading
import time
from pathlib import Path

log = logging.getLogger("phrasehome.backup")
SKIP = ("voice_cache", "train-hearing", "selftest", "__pycache__", ".tmp", "phrasehome-backup-")
PREFIX = "phrasehome-backup-"


def settings(config) -> dict:
    c = dict(config.extra.get("backup") or {})
    return {"auto": bool(c.get("auto", True)), "at": str(c.get("at", "03:30")), "keep": int(c.get("keep", 14)),
            "to": str(c.get("to", "backups"))}


def folder(config, to: str | None = None) -> Path:
    return Path(config.resolve_path(to or settings(config)["to"]))


def make(config, config_path: str | Path, to: str | None = None) -> Path:
    """One backup file. Returns its path."""
    out_dir = folder(config, to)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / time.strftime(f"{PREFIX}%Y%m%d-%H%M%S.tar.gz")
    tmp = out.with_suffix(".partial")

    def keep(ti: tarfile.TarInfo):
        return None if any(s in ti.name for s in SKIP) else ti

    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(str(config_path), arcname="config.yaml")
        for extra in (config.data_dir, "voice_bank", ".env", "credentials.json"):
            p = Path(config.resolve_path(extra))
            if p.exists() and p.resolve() != out_dir.resolve():
                tar.add(str(p), arcname=Path(extra).name if extra != config.data_dir else "data", filter=keep)
    tmp.replace(out)
    out.chmod(0o600)
    return out


def existing(config, to: str | None = None) -> list[Path]:
    d = folder(config, to)
    return sorted(d.glob(f"{PREFIX}*.tar.gz")) if d.is_dir() else []


def prune(config, keep: int, to: str | None = None) -> list[Path]:
    """Delete the oldest backups so `keep` are left. Returns what was deleted."""
    old = existing(config, to)[:-keep] if keep > 0 else []
    for p in old:
        p.unlink(missing_ok=True)
    return old


def restore(config, file: str | Path) -> dict:
    """Put a backup's data back. The current data is moved aside, never deleted."""
    file = Path(file)
    data = Path(config.resolve_path(config.data_dir))
    stamp = time.strftime("%Y%m%d-%H%M%S")
    with tarfile.open(file, "r:gz") as tar:
        members = [m for m in tar.getmembers() if not (m.name.startswith("/") or ".." in Path(m.name).parts)]
        names = {m.name.split("/")[0] for m in members}
        if "data" not in names:
            raise ValueError("that doesn't look like a phrasehome backup (no data folder in it)")
        aside = data.with_name(f"{data.name}.before-restore-{stamp}")
        if data.exists():
            data.rename(aside)
        tmp = data.with_name(f".restore-{stamp}")
        kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        tar.extractall(tmp, members=members, **kw)
    (tmp / "data").rename(data)
    restored_cfg = None
    if (tmp / "config.yaml").exists():
        restored_cfg = Path(config.resolve_path("config.restored.yaml"))
        (tmp / "config.yaml").replace(restored_cfg)
    for name in ("voice_bank",):
        if (tmp / name).exists() and not Path(config.resolve_path(name)).exists():
            (tmp / name).rename(config.resolve_path(name))
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)
    return {"data": str(data), "old_data": str(aside) if aside.exists() else None,
            "config": str(restored_cfg) if restored_cfg else None}


class AutoBackup:
    """Every night at `at`, a backup; the oldest go once there are more than `keep`."""

    def __init__(self, home, config_path: str | None = None) -> None:
        self.home = home
        self.cfg = settings(home.config)
        self.config_path = config_path or str(home.config.base_dir / home.config.source_name)
        self._done = ""
        self._running = False
        self.last: dict = {}

    def start(self) -> None:
        if not self.cfg["auto"]:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        from . import clock

        while self._running:
            now = clock.now()
            day = now.strftime("%Y-%m-%d")
            if now.strftime("%H:%M") >= self.cfg["at"] and self._done != day and not self._made_today(day):
                self._done = day
                self.run_now()
            time.sleep(30)

    def _made_today(self, day: str) -> bool:
        return any(p.name.startswith(PREFIX + day.replace("-", "")) for p in existing(self.home.config))

    def run_now(self) -> dict:
        try:
            p = make(self.home.config, self.config_path)
            gone = prune(self.home.config, self.cfg["keep"])
            self.last = {"time": time.strftime("%Y-%m-%d %H:%M"), "file": p.name, "mb": round(p.stat().st_size / 1e6, 1),
                         "deleted": len(gone)}
            log.info("backup: %s (%.1f MB)", p, p.stat().st_size / 1e6)
        except Exception as e:
            self.last = {"time": time.strftime("%Y-%m-%d %H:%M"), "error": str(e)[:200]}
            log.error("backup failed: %s", e)
        return self.last


def main(args) -> int:
    from .config import load_config

    cfg = load_config(args.config)
    if getattr(args, "restore", None):
        out = restore(cfg, args.restore)
        print(f"Restored into {out['data']}.")
        if out["old_data"]:
            print(f"What was there before is in {out['old_data']} (delete it once you're happy).")
        if out["config"]:
            print(f"The backup's config is in {out['config']}: compare it with config.yaml; yours wasn't touched.")
        print("Restart the house to use it.")
        return 0
    if getattr(args, "list", False):
        files = existing(cfg, args.to)
        for p in files:
            print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB")
        print(f"{len(files)} backups in {folder(cfg, args.to)}" if files else "No backups yet: phrasehome backup")
        return 0
    p = make(cfg, args.config, args.to)
    print(f"Saved {p} ({p.stat().st_size / 1e6:.1f} MB).\nIt contains face data, voice recordings and secrets: "
          f"keep it somewhere private.")
    return 0
