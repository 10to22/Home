"""Updates, safely: pull the latest from GitHub, check it works, and go back if it doesn't.

    phrasehome update            # update now (fast-forward only; refuses if you've changed files yourself)
    phrasehome update --check    # just say whether there's an update
    phrasehome update --test     # also run the test suite before keeping it
    update: { auto: true, at: "04:00", test: false }    # in config.yaml: every night at 4

What it does: git fetch, fast-forward to the new version, reinstall if the dependencies changed, then
checks it (every file compiles, the config still loads, optionally the tests). If anything fails it
puts the old version back. The house restarts itself to use the new version (nightly), or asks you to.
The dashboard's health page has an "Update now" button.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("phrasehome.update")
REPO = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[-300:]}")
    return r.stdout.strip()


def status(repo: Path = REPO) -> dict:
    """{branch, behind, ahead, dirty} after fetching (no changes made)."""
    if not (repo / ".git").exists():
        return {"ok": False, "why": "not a git checkout (installed some other way)"}
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    try:
        _git(repo, "fetch", "--quiet", "origin", branch)
    except RuntimeError as e:
        return {"ok": False, "why": f"can't reach GitHub: {e}", "branch": branch}
    behind = int(_git(repo, "rev-list", "--count", f"HEAD..origin/{branch}") or 0)
    ahead = int(_git(repo, "rev-list", "--count", f"origin/{branch}..HEAD") or 0)
    dirty = bool(_git(repo, "status", "--porcelain", "--untracked-files=no"))
    return {"ok": True, "branch": branch, "behind": behind, "ahead": ahead, "dirty": dirty}


def default_checks(repo: Path, config_path: str | None, tests: bool) -> list[list[str]]:
    py = sys.executable
    out = [[py, "-m", "compileall", "-q", str(repo / "phrasehome")]]
    if config_path and Path(config_path).exists():
        out.append([py, "-m", "phrasehome", "-c", str(config_path), "check"])
    if tests and (repo / "tests").exists():
        out.append([py, "-m", "pytest", "-x", "-q", str(repo / "tests")])
    return out


def update(repo: Path = REPO, config_path: str | None = None, tests: bool = False, force: bool = False,
           checks: list[list[str]] | None = None, install: Callable[[Path], None] | None = None,
           show=print) -> dict:
    """Update to the latest version, or leave things exactly as they were. Returns what happened."""
    st = status(repo)
    if not st.get("ok"):
        return {"updated": False, "why": st.get("why")}
    if st["behind"] == 0:
        return {"updated": False, "why": "already up to date", "branch": st["branch"]}
    if st["dirty"] and not force:
        return {"updated": False, "why": "you've changed files here yourself: commit or stash them first "
                                          "(or update --force)"}
    if st["ahead"]:
        return {"updated": False, "why": f"this copy has {st['ahead']} commits GitHub doesn't: not touching it"}
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "merge", "--ff-only", f"origin/{st['branch']}")
    new = _git(repo, "rev-parse", "HEAD")
    changed = _git(repo, "diff", "--name-only", old, new).splitlines()
    install = install or _pip_install
    if "pyproject.toml" in changed:
        show("dependencies changed: reinstalling")
        install(repo)
    for cmd in checks if checks is not None else default_checks(repo, config_path, tests):
        r = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=1800)
        if r.returncode:
            show(f"check failed ({' '.join(cmd[-3:])}): going back to the old version")
            _git(repo, "reset", "--hard", old)
            if "pyproject.toml" in changed:
                install(repo)
            return {"updated": False, "rolled_back": True, "why": (r.stdout + r.stderr)[-500:],
                    "from": old[:8], "to": new[:8]}
    log.info("updated %s -> %s (%d commits)", old[:8], new[:8], st["behind"])
    return {"updated": True, "from": old[:8], "to": new[:8], "commits": st["behind"], "files": len(changed)}


def _pip_install(repo: Path) -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", str(repo)], check=False, timeout=1800)


def restart() -> None:
    """Start this same program again, on the new code."""
    log.info("restarting to use the new version")
    os.execv(sys.executable, [sys.executable, "-m", "phrasehome", *sys.argv[1:]])


class Nightly:
    """update: {auto: true, at: "04:00", test: false}"""

    def __init__(self, home, config_path: str | None = None) -> None:
        self.home = home
        c = home.config.extra.get("update") or {}
        self.auto = bool(c.get("auto", False))
        self.at = str(c.get("at", "04:00"))
        self.tests = bool(c.get("test", False))
        self.config_path = config_path or str(home.config.base_dir / home.config.source_name)
        self._done = ""
        self._running = False
        self.last: dict = {}

    def start(self) -> None:
        if not self.auto:
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
            if now.strftime("%H:%M") == self.at and self._done != day:
                self._done = day
                self.run_now(restart_after=True)
            time.sleep(20)

    def run_now(self, restart_after: bool = False) -> dict:
        try:
            self.last = {"time": time.strftime("%Y-%m-%d %H:%M"),
                         **update(REPO, self.config_path, self.tests, show=log.info)}
        except Exception as e:
            self.last = {"time": time.strftime("%Y-%m-%d %H:%M"), "updated": False, "why": str(e)[:300]}
        self.home.events.log("update", **{k: v for k, v in self.last.items() if isinstance(v, (str, int, bool))})
        if self.last.get("updated") and restart_after:
            restart()
        return self.last


def main(args) -> int:
    if args.check:
        st = status()
        if not st.get("ok"):
            print(st.get("why"))
        elif st["behind"]:
            print(f"An update is waiting: {st['behind']} new commits on {st['branch']}. Run: phrasehome update")
        else:
            print("Up to date.")
        return 0
    out = update(config_path=args.config, tests=args.test, force=args.force)
    if out.get("updated"):
        print(f"Updated {out['from']} -> {out['to']} ({out['commits']} commits). Restart the house to use it.")
    elif out.get("rolled_back"):
        print(f"The new version failed its checks, so the old one is back.\n{out['why']}")
    else:
        print(f"Not updated: {out['why']}")
    return 0 if not out.get("rolled_back") else 1
