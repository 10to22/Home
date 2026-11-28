"""The house's own bug test: a virtual copy of your house, everything typed at it, problems listed.

    phrasehome selftest            # about a minute. Nothing real is touched: devices are simulated, phones
                                   # and Google go to the log, nothing is spoken, and it works on a copy of data/
    phrasehome selftest --quick    # just every command's own wording, the junk and the pages
    phrasehome selftest --ai       # also asks the real AI three questions (Ollama: free; Claude: a few cents)

What it checks:
- every command is reached by its own wording (two commands sharing one, or a wording so close to another
  command's that "lights off" would run something else)
- the house-life sentences, everyday ones, and junk (blank, emoji, very long) don't crash anything
- every routine runs (its waits skipped)
- every dashboard page loads
- nothing is said or sent with a {placeholder} left in it, no errors are logged, nothing is slow

The last result shows on the health page. Run it after changing config.yaml, or after an update.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome.selftest")

# everyday sentences ({name} / {other}: two of your housemates)
EVERYDAY = [
    "what time is it", "what's the date", "lights on", "turn the lights off", "dim the lights a bit",
    "set a timer for one minute", "cancel the timer", "remind me to call mum tomorrow at 5",
    "what's the wifi password", "party mode", "party's over", "study mode", "how long until my break",
    "study mode off", "house points", "flip a coin", "roll a dice", "pick one of us", "i'm next for the shower",
    "is the bathroom free", "who owes what", "it came to 40 dollars", "how many grams in a cup of flour",
    "350 fahrenheit in celsius", "poll: pizza or tacos", "i vote pizza", "poll results", "house calendar",
    "the inspection is on friday at 10", "new house rule: shoes off inside", "what are the house rules",
    "anyone going to the shops", "get me some milk", "i'm bringing a kettle", "what do we still need",
    "my essay is due friday at 5, 2000 words", "i'm at 500 words", "hangover mode", "hangover mode off",
    "my passport is in the drawer", "where's my passport", "remind me when i get home to take the bins out",
    "shout out to {other} for dinner", "who's up first", "{other}'s a vegetarian", "what do you know about {other}",
    "call a house meeting", "add the bins to the meeting agenda", "what's on the agenda", "cancel the house meeting",
    "shower timer", "stop the shower timer", "i've got a calc exam tomorrow at 9", "any exams coming up",
    "set my door to busy", "is {other} busy", "{other}'s walking home from the library", "who's walking home",
    "house quiz", "stop the quiz", "house radio", "radio off", "should i open the windows", "good night",
    "house, {other}'s allergic to peanuts", "house what's the meaning of life", "lock the front door and turn off the lights",
    "undo that", "no, i meant the bedroom",
]
JUNK = ["", "   ", "house", "unk unk", "😀", "a" * 400, "turn on the {room} lights", "%s %d", "<script>alert(1)</script>",
        "lights on lights on lights on lights on lights on lights on", "?", "house house house"]
SLOW_SECONDS = 3.0
SERIOUS = ("crash", "error", "wrong command", "not understood", "config", "placeholder", "page", "routine")
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


class _Catch(logging.Handler):
    """Warnings and errors logged while each thing is tried, credited to that thing."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.now = ""
        self.records: list[tuple[str, logging.LogRecord]] = []
        self.sent: list[tuple[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if record.levelno >= logging.WARNING:
            self.records.append((self.now, record))
        elif "would send" in msg or "would run" in msg:
            self.sent.append((self.now, msg))


_NOT_SET_UP = re.compile(r"isn't set up|no `\w+:` section|isn't enabled|isn't running|add an? `\w+:` section")


def _kind(record: logging.LogRecord, msg: str) -> str:
    if record.exc_info:
        return "crash"
    if "unknown node" in msg:
        return "config"
    if _NOT_SET_UP.search(msg):
        return "not set up"
    return "error"


def _hint(msg: str) -> str:
    m = re.search(r"unknown node '([^']+)'", msg)
    if m:
        return (f"there's no ESP32 module called '{m.group(1)}': add it under backends: esp: nodes:, or fix the "
                f"room/node name ({msg[:160]})")
    return msg[:300]


def _fill(phrase: str, slots: dict[str, dict[str, str]]) -> str:
    def one(m: re.Match) -> str:
        vals = slots.get(m.group(1)) or {}
        return next((k for k in vals if k != "a"), m.group(1))
    return re.sub(r"\{(\w+)\}", one, phrase)


def _copy_data(src: Path, dst: Path) -> None:
    """The learned wordings, people, points... (small files), not the caches and recordings."""
    if not src.is_dir():
        return
    for p in src.iterdir():
        if p.is_file() and p.suffix in (".json", ".db", ".sqlite", ".jsonl") and p.stat().st_size < 30e6:
            shutil.copy2(p, dst / p.name)


class SelfTest:
    def __init__(self, config_path: str, quick: bool = False, ai: bool = False, show=print) -> None:
        self.config_path = config_path
        self.quick, self.ai, self.show = quick, ai, show
        self.problems: list[dict[str, str]] = []
        self.slow: list[dict[str, Any]] = []
        self.counts = {"wordings": 0, "sentences": 0, "routines": 0, "pages": 0, "ai": 0}

    def problem(self, kind: str, what: str, detail: str = "") -> None:
        self.problems.append({"kind": kind, "what": what[:160], "detail": detail[:300]})

    # ---------- the virtual house ----------
    def _house(self):
        from .config import load_config
        from .dispatcher import Dispatcher
        from .simulator import prepare

        cfg = load_config(self.config_path)
        self.real_data = Path(cfg.resolve_path(cfg.data_dir))
        self.tmp = Path(tempfile.mkdtemp(prefix="phrasehome-selftest-"))
        _copy_data(self.real_data, self.tmp)
        cfg.data_dir = str(self.tmp)
        cfg.settings.cooldown_seconds = 0
        cfg.settings.arbitration_seconds = 0
        cfg.settings.cancel_seconds = 0  # typed commands never wait anyway
        for name, steps in cfg.routines.items():  # waits skipped: the breathing routine takes a minute
            cfg.routines[name] = [s for s in steps if not (isinstance(s, dict) and set(s) == {"wait"})]
        self.sims = prepare(cfg, base_port=0)
        cfg.web = {**cfg.web, "port": 0, "host": "127.0.0.1"}
        home = Dispatcher(cfg)
        home.voice.dry_run = True  # nothing is spoken
        self.said: list[tuple[str, str]] = []
        real_speak = home.voice.speak

        def speak(key, *a, **kw):
            out = real_speak(key, *a, **kw)
            self.said.append((self.catch.now, str(out or "")))
            return out
        home.voice.speak = speak
        home.submit = lambda m: (home._execute(m), True)[1]  # run now, here: errors land on the right sentence
        home.life.sync = True
        if cfg.assistant:
            try:
                from .assistant import Assistant

                home.assistant = Assistant(home)
                if not self.ai:
                    home.assistant.ask = lambda q, room=None, **kw: "(the AI would answer here)"
            except Exception as e:
                self.problem("note", "AI assistant", f"didn't start: {e}")
        return home

    def _try(self, label: str, fn) -> Any:
        self.catch.now = label
        t = time.monotonic()
        try:
            return fn()
        except Exception as e:
            self.problem("crash", label, f"{type(e).__name__}: {e} @ {traceback.extract_tb(e.__traceback__)[-1].name}")
            return None
        finally:
            took = time.monotonic() - t
            if took > SLOW_SECONDS:
                self.slow.append({"what": label[:120], "seconds": round(took, 1)})

    # ---------- the checks ----------
    def wordings(self, web, home) -> None:
        """Every command reached by its own wording."""
        c = home.config
        rooms = list(dict.fromkeys(i.rooms[0] for i in home.instances.all.values() if i.rooms)) or [None]
        for cmd in c.commands:
            here = "{here}" in json.dumps(cmd.action) or "routine" in cmd.action
            for n, phrase in enumerate(cmd.phrases[: 2 if self.quick else 4]):
                said = _fill(phrase, c.slots)
                # "the room you're in" commands: in everyone's room once, since that's where a missing module bites
                for room in (rooms if here and n == 0 else rooms[:1]):
                    self.counts["wordings"] += 1
                    out = self._try(f"wording '{said}'" + (f" in {room}" if room and room != rooms[0] else ""),
                                    lambda s=said, r=room: web.heard(s, r)) or {}
                    self._judge(c, cmd, said, out)

    def _judge(self, c, cmd, said: str, out: dict) -> None:
        got = out.get("matched")
        if got == cmd.name or (got and got in (out.get("all") or [])) or out.get("said"):
            return
        other = next((x for x in c.commands if x.name == got), None)
        if other is not None and other.action == cmd.action:
            return  # two names for the same thing: harmless
        if any(p["what"].startswith(f"'{said}'") for p in self.problems):
            return  # already said (another room)
        if got:
            self.problem("wrong command", f"'{said}' (a wording of {cmd.name})",
                         f"runs {got} instead: change one of the two wordings")
        elif out.get("assistant") or out.get("error"):
            self.problem("not understood", f"'{said}' (a wording of {cmd.name})",
                         "went to the AI instead of running the command")
        else:
            self.problem("not understood", f"'{said}' (a wording of {cmd.name})",
                         "doesn't run anything: is it blocked (settings.block_words) or too long?")

    def sentences(self, web, home) -> None:
        from .config import LIFE_SENTENCES

        people = [i for i in home.instances.all.values() if not i.main]
        other = people[1].name if len(people) > 1 else (people[0].name if people else "Sam")
        name = people[0].name if people else "Sam"
        rooms = [None] + [i.rooms[0] for i in people if i.rooms][: (1 if self.quick else 3)]
        names = (home.config.assistant or {}).get("names") or ["house"]
        texts = list(JUNK)
        if not self.quick:
            for s in EVERYDAY:
                s = s.replace("{other}", other).replace("{name}", name)
                texts.append(str(names[0]) + s[5:] if s.startswith("house") else s)  # your house's own name
            texts += [ex for _, exs in LIFE_SENTENCES for ex in exs[:2]]
        for room in rooms:
            for t in texts:
                self.counts["sentences"] += 1
                self._try(f"'{t[:60]}'" + (f" in the {room}" if room else " at the hub"), lambda t=t, r=room: web.heard(t, r))

    def routines(self, home) -> None:
        for name, steps in home.config.routines.items():
            self.counts["routines"] += 1
            failed = self._try(f"routine {name}", lambda s=steps: home.run_steps(s, {"here": home.default_room()}, "selftest"))
            if failed:
                self.problem("routine", f"routine {name}", f"{failed} step(s) failed (see the errors below)")

    def pages(self, home) -> None:
        import requests

        from . import web as webmod
        from .web import WebServer

        server = WebServer(home)
        server.start()
        try:
            src = Path(webmod.__file__).read_text()
            src = src[src.index("def do_GET"):src.index("def do_", src.index("def do_GET") + 10)]
            paths = sorted(set(re.findall(r'u\.path == "(/[^"]*)"', src)) - {"/api/live", "/api/camera", "/api/camera/stream",
                                                                          "/api/companion/poll", "/api/update", "/api/photo"})
            h = {"X-Token": server.token} if server.token else {}
            for p in paths:
                self.counts["pages"] += 1
                self.catch.now = f"page {p}"
                try:
                    r = requests.get(f"http://127.0.0.1:{server.port}{p}", headers=h, timeout=30)
                    if r.status_code >= 500 or (r.status_code >= 400 and "isn't running" not in r.text
                                                and "not set up" not in r.text):
                        self.problem("page", p, f"{r.status_code}: {r.text[:160]}")
                except Exception as e:
                    self.problem("page", p, str(e)[:200])
        finally:
            server.stop()

    def ask_ai(self, home) -> None:
        from .assistant import ai_problem

        ai = home.assistant
        if ai is None:
            self.problem("ai", "AI", "no assistant: section in config.yaml")
            return
        for q in ("what's the weather like today", "who lives here", "tell me something funny about mondays"):
            self.counts["ai"] += 1
            self.catch.now = f"AI: {q}"
            t = time.monotonic()
            try:
                a = ai.ask(q, home.default_room())
                took = time.monotonic() - t
                self.show(f"  AI ({took:.1f} s): {q} -> {a[:120]}")
                if took > 12:
                    self.slow.append({"what": f"AI: {q}", "seconds": round(took, 1)})
            except Exception as e:
                self.problem("ai", q, ai_problem(e, home.config))

    # ---------- after ----------
    def _review(self) -> None:
        for where, text in self.said:
            if _PLACEHOLDER.search(text) and "{room}" not in where:
                self.problem("placeholder", where or "(background)", f"said: {text[:150]}")
        for where, msg in self.catch.sent:
            if _PLACEHOLDER.search(msg.split(":", 1)[-1]) and "{room}" not in where:
                self.problem("placeholder", where or "(background)", f"sent: {msg[:150]}")
        seen = set()
        for where, r in self.catch.records:
            msg = r.getMessage()
            key = (r.levelname, msg[:120])
            if key in seen:
                continue
            seen.add(key)
            if r.levelno >= logging.ERROR:
                self.problem(_kind(r, msg), where or "(background)", _hint(msg))
            elif not any(x in msg for x in ("Max retries", "HTTPSConnectionPool", "can't play sound", "Piper voice file",
                                             "nobody to notify", "voice recognition isn't set up")):
                self.problem("warning", where or "(background)", msg[:300])

    def _group(self) -> None:
        """One line per missing module, listing what it breaks, instead of one per command."""
        by_node: dict[str, list[str]] = {}
        keep = []
        for p in self.problems:
            m = re.search(r"no ESP32 module called '([^']+)'", p["detail"]) if p["kind"] == "config" else None
            if m:
                said = re.search(r"'([^']*)'", p["what"])
                by_node.setdefault(m.group(1), []).append(said.group(1) if said else p["what"])
            else:
                keep.append(p)
        for node, said in by_node.items():
            uniq = list(dict.fromkeys(said))
            keep.append({"kind": "config", "what": f"the {node}",
                         "detail": f"has no ESP32 module, so {len(uniq)} things fail there ({', '.join(uniq[:5])}"
                                   f"{'...' if len(uniq) > 5 else ''}): add '{node}' under backends: esp: nodes:, "
                                   f"or point that room at the module it uses"})
        self.problems = keep

    def run(self) -> dict:
        start = time.monotonic()
        self.catch = _Catch()
        root = logging.getLogger("phrasehome")
        root.addHandler(self.catch)
        old_level, old_prop = root.level, root.propagate
        root.setLevel(logging.INFO)  # to see what the log backends would have sent...
        root.propagate = False       # ...without printing all of it
        home = None
        try:
            self.show("Building a virtual copy of the house...")
            home = self._try("starting the house", self._house)
            if home is None:
                return self._result(start)
            from .web import WebServer

            web = WebServer.__new__(WebServer)
            web.home = home
            self.show("Every command's own wording...")
            self.wordings(web, home)
            self.show("Sentences, house life and junk...")
            self.sentences(web, home)
            if not self.quick:
                self.show("Routines...")
                self.routines(home)
            self.show("Dashboard pages...")
            self.pages(home)
            if self.ai:
                self.show("Asking the AI...")
                self.ask_ai(home)
            time.sleep(0.3)
        finally:
            root.removeHandler(self.catch)
            root.setLevel(old_level)
            root.propagate = old_prop
            if home is not None:
                try:
                    home.close()
                except Exception:
                    pass
            for s in getattr(self, "sims", {}).values():
                try:
                    s.close()
                except Exception:
                    pass
            shutil.rmtree(getattr(self, "tmp", ""), ignore_errors=True)
        self._review()
        self._group()
        return self._result(start)

    def _result(self, start: float) -> dict:
        serious = [p for p in self.problems if p["kind"] in SERIOUS]
        out = {"time": time.strftime("%Y-%m-%d %H:%M"), "ok": not serious, "seconds": round(time.monotonic() - start),
               "checked": self.counts, "problems": self.problems, "slow": sorted(self.slow, key=lambda s: -s["seconds"])[:10],
               "crashes": sum(p["kind"] == "crash" for p in self.problems), "quick": self.quick}
        try:
            (getattr(self, "real_data", None) or Path("data")).mkdir(parents=True, exist_ok=True)
            (getattr(self, "real_data", None) or Path("data")).joinpath("selftest.json").write_text(json.dumps(out, indent=1))
        except OSError:
            pass
        return out


def last_result(config) -> dict | None:
    try:
        return json.loads((Path(config.resolve_path(config.data_dir)) / "selftest.json").read_text())
    except (OSError, ValueError):
        return None


def print_report(r: dict, show=print) -> None:
    c = r["checked"]
    show(f"\nChecked {c['wordings']} command wordings, {c['sentences']} sentences, {c['routines']} routines and "
         f"{c['pages']} pages in {r['seconds']} s.")
    order = {k: i for i, k in enumerate(SERIOUS + ("ai", "not set up", "warning", "note"))}
    for p in sorted(r["problems"], key=lambda p: order.get(p["kind"], 99)):
        show(f"  [{p['kind']}] {p['what']}: {p['detail']}")
    for s in r["slow"]:
        show(f"  [slow] {s['what']}: {s['seconds']} s")
    if r["ok"]:
        show("All good." + (" (Some warnings above are worth a look.)" if r["problems"] else ""))
    else:
        show("Problems found. crash / error: bugs (send Claude this output). wrong command, not understood, "
             "config: fixed in config.yaml. not set up: a command for a feature with no section in config.yaml "
             "(fine if you don't use it).")


def main(args) -> int:
    r = SelfTest(args.config, quick=args.quick, ai=args.ai,
                 show=(lambda *a: None) if args.json else print).run()
    if args.json:
        print(json.dumps(r))
    else:
        print_report(r)
    return 0 if r["ok"] else 1
