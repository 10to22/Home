"""Keep the house running in the background: starts when you log in, restarts itself if it ever stops.

    phrasehome service install                   # `phrasehome run` in the background (the real house)
    phrasehome service install --simulate --mic  # or the virtual house with this laptop's mic, for testing
    phrasehome service status                    # running? since when? the last lines of its log
    phrasehome service logs                      # more of the log
    phrasehome service stop / start / uninstall

Mac: a LaunchAgent (~/Library/LaunchAgents/com.phrasehome.house.plist), log in ~/Library/Logs/phrasehome.log.
The first time it starts, macOS may ask whether Python can use the microphone: say yes. If the log says the
mic only hears silence, turn Python on in System Settings > Privacy & Security > Microphone.
Raspberry Pi: the boot service in deploy/ (sudo ./deploy/enable-service.sh), which this runs for you.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "com.phrasehome.house"
REPO = Path(__file__).resolve().parents[1]


def plist_path() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    if platform.system() == "Darwin":
        return Path.home() / "Library/Logs/phrasehome.log"
    return Path.home() / ".local/state/phrasehome.log"


def command(config: str, simulate: bool = False, mic: bool = False, extra: list[str] | None = None) -> list[str]:
    cfg = str(Path(config).resolve())
    if simulate:
        return [sys.executable, "-m", "phrasehome", "-c", cfg, "simulate", *(["--mic"] if mic else []), *(extra or [])]
    return [sys.executable, "-m", "phrasehome", "-c", cfg, "run", *(extra or [])]


def plist(argv: list[str], workdir: str) -> str:
    # launchd starts with a bare PATH: add Homebrew's, or ffmpeg and friends go missing
    path = ":".join(dict.fromkeys(["/opt/homebrew/bin", "/usr/local/bin", *os.environ.get("PATH", "").split(":"),
                                   "/usr/bin", "/bin", "/usr/sbin", "/sbin"]))
    args = "".join(f"\n    <string>{escape(a)}</string>" for a in argv)
    log = escape(str(log_path()))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}
  </array>
  <key>WorkingDirectory</key><string>{escape(workdir)}</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{escape(path)}</string>
    <key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(config: str, simulate: bool = False, mic: bool = False, extra: list[str] | None = None) -> str:
    sysname = platform.system()
    if sysname == "Darwin":
        p = plist_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        log_path().parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            _launchctl("unload", str(p))
        p.write_text(plist(command(config, simulate, mic, extra), str(Path(config).resolve().parent)))
        r = _launchctl("load", "-w", str(p))
        if r.returncode:
            return f"wrote {p}, but launchctl said: {(r.stderr or r.stdout).strip()}"
        return (f"Installed: the house now runs in the background and starts when you log in.\n"
                f"Log: {log_path()}   Check it: phrasehome service status\n"
                f"If macOS asks whether Python can use the microphone, say yes.")
    if sysname == "Linux":
        script = REPO / "deploy/enable-service.sh"
        if simulate:
            return "On Linux the background service runs the real house (phrasehome run), not the simulator."
        if not shutil.which("systemctl") or not script.exists():
            return "This computer has no systemd: start it with `phrasehome run` from a terminal (or tmux)."
        if os.geteuid() != 0:
            return f"Run this (it needs sudo):\n  sudo {script}"
        r = subprocess.run([str(script)], capture_output=True, text=True)
        return (r.stdout + r.stderr).strip() or "installed"
    return "Don't know how to run things in the background on this computer."


def uninstall() -> str:
    if platform.system() == "Darwin":
        p = plist_path()
        if not p.exists():
            return "It wasn't installed."
        _launchctl("unload", "-w", str(p))
        p.unlink()
        return "Removed: the house no longer runs in the background."
    return "On the Pi: sudo systemctl disable --now phrasehome"


def start_stop(start: bool) -> str:
    if platform.system() == "Darwin":
        p = plist_path()
        if not p.exists():
            return "Not installed yet: phrasehome service install"
        r = _launchctl("load" if start else "unload", str(p))
        return ("started" if start else "stopped (until you log in again, or: phrasehome service start)") \
            if not r.returncode else (r.stderr or r.stdout).strip()
    return f"sudo systemctl {'start' if start else 'stop'} phrasehome"


def status(lines: int = 15) -> str:
    out = []
    if platform.system() == "Darwin":
        if not plist_path().exists():
            return "Not installed: phrasehome service install"
        r = _launchctl("list", LABEL)
        if r.returncode:
            out.append("Installed but not running (phrasehome service start).")
        else:
            pid = next((ln.split("=")[1].strip(" ;") for ln in r.stdout.splitlines() if '"PID"' in ln), "")
            last = next((ln.split("=")[1].strip(" ;") for ln in r.stdout.splitlines() if "LastExitStatus" in ln), "0")
            out.append(f"Running (process {pid})." if pid else f"Not running right now (last exit status {last}): "
                                                           "it restarts by itself; see the log below.")
    elif shutil.which("systemctl"):
        r = subprocess.run(["systemctl", "is-active", "phrasehome"], capture_output=True, text=True)
        out.append(f"phrasehome service: {r.stdout.strip() or 'not installed'} (logs: journalctl -u phrasehome -f)")
        return "\n".join(out)
    log = log_path()
    if log.exists():
        tail = log.read_text(errors="replace").splitlines()[-lines:]
        out.append(f"\nLast lines of {log}:")
        out += [f"  {t}" for t in tail]
    return "\n".join(out)


def main(args) -> int:
    a = args.action
    if a == "install":
        print(install(args.config, simulate=args.simulate, mic=args.mic))
    elif a == "uninstall":
        print(uninstall())
    elif a in ("start", "stop"):
        print(start_stop(a == "start"))
    elif a == "logs":
        print(status(lines=120))
    else:
        print(status())
    return 0
