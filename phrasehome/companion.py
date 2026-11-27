"""The laptop companion: runs in the background on each person's laptop and helps when it's needed.

    phrasehome companion --person finn --hub http://house.local:8080          # run it
    phrasehome companion --person finn --hub http://house.local:8080 --install  # start at every login
    phrasehome companion --uninstall

What it does
  - Knows what kind of thing is on screen (from the app and window title, checked on the laptop): study,
    code, a call, a distraction, or private. Only that kind (and the app's name) goes to the house: never
    window titles, and never anything from private apps (password managers, banking, messages, incognito).
  - In a call (Zoom, Teams, Meet, FaceTime): your door light says busy until you hang up.
  - In study mode or the last day before a deadline: a few minutes on YouTube, TikTok, Netflix, Reddit...
    gets a nudge, on the laptop and in your room.
  - "house, look at my screen" / "what's this error?" / "explain this slide" (said in your room): the
    companion takes one screenshot, the AI looks at it, and the answer is said in your room and shown
    as a notification. Nothing is captured unless you ask, and never while a private app is in front.
  - "house, read my screen": reads the text on screen aloud (needs OCR: pip install ocrmac on a Mac,
    or pytesseract).
  - With OCR: your document's word count goes to your deadline's pace by itself, and if the same error
    sits in your terminal for a couple of minutes it offers to help (--auto-help explains it straight away).
  - "house, stop watching my screen" pauses it (it keeps telling the house it's paused, nothing else).

On a Mac: allow your terminal (or Python) in System Settings > Privacy & Security > Screen Recording, and
Accessibility for window titles. Hotkey: none needed; it listens to the house.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.companion")

CATEGORIES = [
    ("private", r"1password|bitwarden|lastpass|keepass|keychain|dashlane|bank|paypal|venmo|revolut|monzo|"
                r"messages|whatsapp|signal|telegram|private browsing|incognito|inprivate|tinder|hinge|bumble"),
    ("call", r"zoom|microsoft teams|teams|google meet|meet\.google|facetime|webex|skype|discord.*(?:call|voice)|jitsi"),
    ("distraction", r"youtube|tiktok|instagram|netflix|twitch|reddit|twitter|x\.com|facebook|disney\+|hulu|"
                    r"prime video|steam|epic games|roblox|minecraft|league of legends|valorant|fortnite|9gag|tumblr|"
                    r"crunchyroll|spotify.*podcast"),
    ("code", r"visual studio|vs ?code|code|pycharm|intellij|xcode|terminal|iterm|warp|powershell|cmd\.exe|"
             r"jupyter|rstudio|android studio|sublime|vim|emacs|cursor"),
    ("study", r"word|pages|google docs|docs\.google|notion|overleaf|obsidian|onenote|canvas|moodle|blackboard|"
              r"brightspace|preview|acrobat|\.pdf|kindle|anki|quizlet|zotero|mendeley|scholar|jstor|wikipedia|"
              r"keynote|powerpoint|slides|excel|numbers|sheets|chatgpt|claude"),
]
ERROR_RX = re.compile(r"Traceback \(most recent call last\)|\b\w*Error: |\bException\b|error\[E\d+\]|"
                      r"\bFAILED\b|Segmentation fault|command not found|npm ERR!|fatal: |cannot find symbol")
WORDS_RX = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d{2,6}) words?\b", re.I)


def classify(app: str, title: str = "") -> str:
    text = f"{app} {title}".lower()
    for cat, rx in CATEGORIES:
        if re.search(rx, text):
            return cat
    return "other"


# ---------------------------------------------------------------- the laptop's side
def active_window() -> tuple[str, str]:
    """(app name, window title) of the front window, best effort on each OS."""
    sysname = platform.system()
    try:
        if sysname == "Darwin":
            app = subprocess.run(["osascript", "-e", 'tell application "System Events" to get name of first '
                                  "process whose frontmost is true"], capture_output=True, text=True, timeout=3).stdout.strip()
            title = subprocess.run(["osascript", "-e", 'tell application "System Events" to tell (first process whose '
                                    'frontmost is true) to get name of front window'], capture_output=True, text=True,
                                   timeout=3).stdout.strip()
            return app, title
        if sysname == "Linux" and shutil.which("xdotool"):
            title = subprocess.run(["xdotool", "getactivewindow", "getwindowname"], capture_output=True, text=True,
                                   timeout=3).stdout.strip()
            return title.split(" - ")[-1] if " - " in title else title, title
        if sysname == "Windows":
            import ctypes

            hwnd = ctypes.windll.user32.GetForegroundWindow()
            n = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            return title.split(" - ")[-1] if " - " in title else title, title
    except Exception as e:
        log.debug("active window: %s", e)
    return "", ""


def screenshot(max_side: int = 1600) -> bytes | None:
    """One JPEG of the screen, or None if this computer won't allow it."""
    data = None
    try:
        if platform.system() == "Darwin" and shutil.which("screencapture"):
            with tempfile.TemporaryDirectory() as d:
                f = Path(d) / "s.jpg"
                subprocess.run(["screencapture", "-x", "-t", "jpg", str(f)], timeout=10, check=True)
                data = f.read_bytes()
        else:
            from PIL import ImageGrab

            buf = io.BytesIO()
            ImageGrab.grab().convert("RGB").save(buf, "JPEG", quality=80)
            data = buf.getvalue()
    except Exception as e:
        log.warning("couldn't take a screenshot (on a Mac, allow Screen Recording): %s", e)
        return None
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=80)
        return out.getvalue()
    except Exception:
        return data


def ocr(jpeg: bytes) -> str | None:
    """Text on screen, if this laptop has OCR (ocrmac on a Mac, or pytesseract)."""
    try:
        from ocrmac import ocrmac  # Apple's Vision framework
        from PIL import Image

        return "\n".join(t[0] for t in ocrmac.OCR(Image.open(io.BytesIO(jpeg))).recognize())
    except ImportError:
        pass
    except Exception as e:
        log.debug("ocrmac: %s", e)
    try:
        import pytesseract
        from PIL import Image

        return pytesseract.image_to_string(Image.open(io.BytesIO(jpeg)))
    except Exception:
        return None


def notify(text: str, title: str = "House") -> None:
    try:
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title {json.dumps(title)}"],
                           timeout=5)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", title, text], timeout=5)
        else:
            print(f"[{title}] {text}")
    except Exception:
        print(f"[{title}] {text}")


class Companion:
    def __init__(self, hub: str, person: str, token: str = "", every: float = 5.0, auto_help: bool = False,
                 speak_here: bool = False) -> None:
        self.hub, self.person, self.token = hub.rstrip("/"), person.lower(), token
        self.every, self.auto_help, self.speak_here = every, auto_help, speak_here
        self.cat, self.since, self.app = "other", time.time(), ""
        self.paused = False
        self._last_report = 0.0
        self._last_ocr = 0.0
        self._error_seen: tuple[str, int] = ("", 0)
        self._running = False

    def _post(self, path: str, body: dict) -> dict:
        import requests

        r = requests.post(self.hub + path, json={**body, "person": self.person}, timeout=60,
                          headers={"X-Token": self.token} if self.token else {})
        r.raise_for_status()
        return r.json()

    def _get(self, path: str) -> dict:
        import requests

        r = requests.get(self.hub + path, params={"person": self.person}, timeout=30,
                         headers={"X-Token": self.token} if self.token else {})
        r.raise_for_status()
        return r.json()

    def report(self) -> dict:
        app, title = active_window()
        cat = classify(app, title)
        if cat != self.cat:
            self.cat, self.since = cat, time.time()
        self.app = "" if cat == "private" else app
        if time.time() - self._last_report < 30 and cat == getattr(self, "_reported_cat", None):
            return {}
        self._last_report, self._reported_cat = time.time(), cat
        out = self._post("/api/companion/report", {"category": cat, "app": self.app,
                                                   "minutes": round((time.time() - self.since) / 60, 1)})
        for msg in out.get("notify", []):
            notify(msg)
        self.paused = bool(out.get("paused"))
        return out

    def handle(self, req: dict) -> None:
        if self.cat == "private":
            self._post("/api/companion/screen", {"id": req["id"], "refused": "a private app is in front"})
            return
        shot = screenshot()
        if shot is None:
            self._post("/api/companion/screen", {"id": req["id"], "refused": "this laptop won't allow screenshots yet"})
            return
        body: dict[str, Any] = {"id": req["id"]}
        if req.get("kind") == "read":
            body["text"] = ocr(shot) or ""
            if not body["text"]:
                body["image"] = base64.b64encode(shot).decode()
        else:
            body["image"] = base64.b64encode(shot).decode()
        out = self._post("/api/companion/screen", body)
        if out.get("answer"):
            notify(out["answer"][:240])
            if self.speak_here and shutil.which("say"):
                subprocess.Popen(["say", out["answer"]])

    def watch_text(self) -> None:
        """With OCR: the document's word count, and an error that's been sitting there."""
        if self.cat not in ("study", "code") or time.time() - self._last_ocr < 90:
            return
        self._last_ocr = time.time()
        shot = screenshot(1400)
        text = ocr(shot) if shot else None
        if not text:
            return
        if self.cat == "study":
            m = WORDS_RX.findall(text)
            if m:
                n = max(int(x.replace(",", "")) for x in m)
                self._post("/api/companion/report", {"category": self.cat, "app": self.app, "words": n})
        if self.cat == "code":
            m = ERROR_RX.search(text)
            if not m:
                self._error_seen = ("", 0)
                return
            line = text[max(0, m.start() - 80): m.end() + 160]
            key = re.sub(r"\d+", "", m.group(0))
            seen, n = self._error_seen
            self._error_seen = (key, n + 1 if seen == key else 1)
            if self._error_seen[1] == 2:
                if self.auto_help and shot:
                    out = self._post("/api/house/photo", {"image": base64.b64encode(shot).decode(), "kind": "screen",
                                                          "question": "What does this error mean and how do I fix it?",
                                                          "speak": False})
                    notify(out.get("answer", "")[:240], "About that error")
                else:
                    notify("Stuck on that error? Say 'house, what's this error?' and I'll take a look.", "House")
                    self._post("/api/companion/report", {"category": self.cat, "app": self.app, "stuck": line[:300]})

    def run(self) -> None:
        self._running = True
        notify(f"Companion running for {self.person}. Say 'house, stop watching my screen' to pause it.")
        while self._running:
            try:
                self.report()
                if not self.paused:
                    for req in self._get("/api/companion/poll").get("requests", []):
                        threading.Thread(target=self.handle, args=(req,), daemon=True).start()
                    self.watch_text()
            except Exception as e:
                log.warning("companion: %s", e)
                time.sleep(10)
            time.sleep(self.every)


# ---------------------------------------------------------------- starting at login
def install(argv: list[str]) -> str:
    exe = [sys.executable, "-m", "phrasehome"] + [a for a in argv if a not in ("--install",)]
    sysname = platform.system()
    if sysname == "Darwin":
        p = Path.home() / "Library/LaunchAgents/com.phrasehome.companion.plist"
        args = "".join(f"<string>{a}</string>" for a in exe)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>Label</key><string>com.phrasehome.companion</string>
<key>ProgramArguments</key><array>{args}</array><key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardErrorPath</key><string>{Path.home()}/Library/Logs/phrasehome-companion.log</string></dict></plist>
""")
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        subprocess.run(["launchctl", "load", str(p)], capture_output=True)
        return f"installed: {p} (starts at login; log in ~/Library/Logs/phrasehome-companion.log)"
    if sysname == "Linux":
        p = Path.home() / ".config/systemd/user/phrasehome-companion.service"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"[Unit]\nDescription=phrasehome companion\n\n[Service]\nExecStart={' '.join(exe)}\nRestart=always\n\n"
                     "[Install]\nWantedBy=default.target\n")
        subprocess.run(["systemctl", "--user", "enable", "--now", "phrasehome-companion"], capture_output=True)
        return f"installed: {p}"
    if sysname == "Windows":
        p = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/phrasehome-companion.bat"
        p.write_text("@echo off\nstart \"\" /min " + " ".join(f'"{a}"' for a in exe) + "\n")
        return f"installed: {p}"
    return "don't know how to start things at login on this computer"


def uninstall() -> str:
    for p in (Path.home() / "Library/LaunchAgents/com.phrasehome.companion.plist",
              Path.home() / ".config/systemd/user/phrasehome-companion.service",
              Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup/phrasehome-companion.bat"):
        if p.exists():
            if p.suffix == ".plist":
                subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
            if p.suffix == ".service":
                subprocess.run(["systemctl", "--user", "disable", "--now", "phrasehome-companion"], capture_output=True)
            p.unlink()
            return f"removed {p}"
    return "it wasn't installed"


# ---------------------------------------------------------------- the house's side
class CompanionHub:
    """What the house does with what the companions tell it (home.life.companion)."""

    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("companion") or {})
        self.nudge_after = float(self.cfg.get("nudge_minutes", 3))
        self.state: dict[str, dict] = {}
        self._requests: dict[str, list] = {}
        self._waiting: dict[str, dict] = {}
        self._lock = threading.Lock()

    def online(self, pid: str) -> bool:
        st = self.state.get(pid)
        return bool(st) and time.time() - st["seen"] < 120

    def _room(self, inst) -> str | None:
        return inst.rooms[0] if inst and inst.rooms else None

    def report(self, pid: str, body: dict) -> dict:
        h = self.home
        inst = h.instances.all.get(pid)
        if inst is None:
            raise KeyError(f"nobody called {pid} lives here")
        st = self.state.setdefault(pid, {"cat": "other", "since": time.time(), "paused": False, "nudged": 0,
                                         "today": {}, "day": ""})
        cat = str(body.get("category") or "other")
        now = time.time()
        day = time.strftime("%Y-%m-%d")
        if st["day"] != day:
            st["day"], st["today"] = day, {}
        if "seen" in st:
            st["today"][st["cat"]] = st["today"].get(st["cat"], 0) + min(now - st["seen"], 60)
        if cat != st["cat"]:
            st["cat"], st["since"] = cat, now
        st["seen"], st["app"] = now, str(body.get("app") or "")
        out: dict[str, Any] = {"paused": st["paused"], "notify": []}
        if st["paused"]:
            return out
        life = h.life
        room = self._room(inst)
        # calls: the door says busy while you're on one
        if room:
            manual = life.door_status.manual.get(room)
            if cat == "call" and not manual:
                life.door_status.manual[room] = ("busy", 0.0)
                st["call"] = True
            elif cat != "call" and st.get("call"):
                st["call"] = False
                if manual and manual[0] == "busy" and not manual[1]:
                    life.door_status.manual.pop(room, None)
        # distractions while studying or crunching
        focused = (room and life.study.studying(room)) or life.deadlines.crunching(inst)
        if focused and cat == "distraction" and now - st["since"] >= self.nudge_after * 60 and now - st["nudged"] > 600:
            st["nudged"] = now
            text = h.voice.resolve("companion_nudge", name=inst.name, app=st["app"] or "that",
                                   minutes=round((now - st["since"]) / 60))[0]
            out["notify"].append(text)
            h.voice.speak("companion_nudge", room=room, name=inst.name, app=st["app"] or "that",
                          minutes=round((now - st["since"]) / 60))
            h.events.log("companion.nudge", person=pid, app=st["app"])
        if body.get("words"):
            life.deadlines.words(inst, int(body["words"]))
        if body.get("stuck"):
            h.events.log("companion.stuck", person=pid)
        return out

    # ---------- "house, look at my screen" ----------
    def ask_screen(self, inst, question: str, kind: str = "screen", room: str | None = None) -> bool:
        if not self.online(inst.id) or self.state[inst.id]["paused"]:
            return False
        rid = f"{inst.id}-{int(time.time() * 1000)}"
        with self._lock:
            self._requests.setdefault(inst.id, []).append({"id": rid, "kind": kind, "question": question})
            self._waiting[rid] = {"person": inst.id, "question": question, "kind": kind, "room": room or self._room(inst)}
        return True

    def poll(self, pid: str) -> dict:
        with self._lock:
            reqs, self._requests[pid] = self._requests.get(pid, []), []
        if pid in self.state:
            self.state[pid]["seen"] = time.time()
        return {"requests": reqs, "paused": self.state.get(pid, {}).get("paused", False)}

    def screen(self, pid: str, body: dict) -> dict:
        h = self.home
        w = self._waiting.pop(str(body.get("id")), None)
        if w is None or w["person"] != pid:
            raise KeyError("no such request")
        room = w["room"]
        if body.get("refused"):
            h.voice.speak("companion_refused", room=room, why=body["refused"])
            return {"answer": ""}
        if w["kind"] == "read" and body.get("text"):
            h.life.reader.start(room, str(body["text"]), "your screen")
            return {"answer": "Reading it out."}
        if not body.get("image"):
            return {"answer": ""}
        kind = "slide" if w["kind"] == "slide" else "screen"
        answer = h.life.vision.ask(base64.b64decode(body["image"]), w["question"], kind, room, pid)
        return {"answer": answer}

    def heard(self, said: str, room, speaker):
        s = said.lower().replace("’", "'").strip(" .!?")
        if speaker is None or speaker.main:
            return None
        if re.match(r"^(?:stop|pause) (?:watching|looking at) my (?:screen|laptop)|^pause (?:the|my) companion", s):
            self.state.setdefault(speaker.id, {"seen": 0, "cat": "other", "since": time.time(), "nudged": 0,
                                               "today": {}, "day": ""})["paused"] = True
            return "companion_paused", {}
        if re.match(r"^(?:start|resume) (?:watching|looking at) my (?:screen|laptop)|^(?:resume|unpause) (?:the|my) companion", s):
            if speaker.id in self.state:
                self.state[speaker.id]["paused"] = False
            return "companion_resumed", {}
        if re.match(r"^(?:how long have i been|how much time have i spent) (?:on|procrastinating)|^(?:focus|screen) report", s):
            st = self.state.get(speaker.id)
            if not st:
                return "companion_offline", {}
            mins = {k: round(v / 60) for k, v in st["today"].items() if v >= 60}
            return "companion_report", {"study": mins.get("study", 0) + mins.get("code", 0),
                                        "distraction": mins.get("distraction", 0), "calls": mins.get("call", 0)}
        kind = None
        if re.match(r"^(?:explain|what's|what is) (?:this|the) slide", s):
            kind = "slide"
        elif re.match(r"^read (?:my|the) screen|^read (?:this|what's on my screen) (?:to me|out)", s):
            kind = "read"
        elif re.match(r"^(?:look at|check|what's on|whats on|explain) my (?:screen|laptop)|^what(?:'s| is) this error|"
                      r"^why (?:isn't|is not|won't) (?:this|my code) (?:work|working|run|running)|^(?:help me with|can you see) my screen|"
                      r"^what am i looking at|^summari[sz]e (?:this|my screen|this page)", s):
            kind = "screen"
        if kind is None:
            return None
        if not self.online(speaker.id):
            return "companion_offline", {}
        self.ask_screen(speaker, said, kind, room)
        return "companion_looking", {}

    def status(self) -> list[dict]:
        out = []
        for pid, st in self.state.items():
            out.append({"person": pid, "online": self.online(pid), "category": st["cat"], "paused": st["paused"],
                        "today": {k: round(v / 60) for k, v in st["today"].items()}})
        return out


def main(args) -> int:
    if getattr(args, "uninstall", False):
        print(uninstall())
        return 0
    if not args.person:
        print("Say whose laptop this is: phrasehome companion --person finn --hub http://<the house>:8080")
        return 2
    if getattr(args, "install", False):
        print(install(sys.argv[1:]))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    token = args.token or os.environ.get("PHRASEHOME_TOKEN", "")
    Companion(args.hub, args.person, token, args.every, args.auto_help, args.speak_here).run()
    return 0
