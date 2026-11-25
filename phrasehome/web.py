"""The hub's web dashboard and HTTP API (default http://<pi>:8800).

Open it on a phone or a wall tablet: doors, house mode, who's home, every room's reminders and
lists, recent activity with door-scan photos, offline devices, routine buttons, and a box to
make the house say something or ask the AI.

Set a token (web.token_env, default PHRASEHOME_TOKEN) so only your household can use it; the
first visit asks for it once. The API is also handy for phone shortcuts ("Hey Siri, ask the house"):

  POST /api/ask      {"question": "...", "room": "kitchen", "speak": false}  -> {"answer": "..."}
  POST /api/routine  {"name": "lockdown"}          POST /api/mode    {"mode": "away"}
  POST /api/door     {"door": "front", "state": "locked"}
  POST /api/say      {"text": "Dinner's ready", "everywhere": true}
  POST /api/list     {"list": "shopping", "add": ["milk"], "person": ""}
  POST /api/event    {"kind": "garage_opened"}     -> automations `when: {event: external.garage_opened}`
  GET  /api/status
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("phrasehome.web")


def climate(home) -> dict:
    """Temperature/humidity per room from device-node sensors (inputs named temperature/humidity)."""
    out: dict[str, dict] = {}
    if not home.sensors:
        return out
    f = str((home.config.extra.get("weather") or {}).get("units", "fahrenheit")).startswith("f")
    rooms = getattr(home, "rooms", None)
    for (node, inp), v in list(home.sensors.values.items()):
        if "value" not in v:
            continue
        room = rooms.room_of_node(node) if rooms else node
        if inp.startswith("temp"):
            out.setdefault(room, {})["temperature"] = round(v["value"] * 9 / 5 + 32 if f else v["value"], 1)
        elif inp.startswith("humid"):
            out.setdefault(room, {})["humidity"] = round(v["value"])
    return out


class WebServer:
    def __init__(self, home) -> None:
        self.home = home
        cfg = home.config.web
        self.port = int(cfg.get("port", 8800))
        self.host = str(cfg.get("host", "0.0.0.0"))
        self.token = str(cfg.get("token") or os.environ.get(cfg.get("token_env", "PHRASEHOME_TOKEN"), ""))
        self.api_unlock = bool(cfg.get("api_unlock", False))
        self.httpd: ThreadingHTTPServer | None = None

    def start(self) -> None:
        server = self

        class Handler(_Handler):
            web = server

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log.info("dashboard: http://<this pi>:%d%s", self.port,
                 "" if self.token else "  (no token set: anyone on your network can use it)")

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()

    # ---------- what the dashboard shows ----------
    def status(self) -> dict:
        h = self.home
        insts = []
        for i in h.instances.all.values():
            insts.append({"id": i.id, "name": i.name, "rooms": i.rooms, "main": i.main,
                          "dnd": h.instances.dnd(i), "quiet": i.quiet_now(),
                          "agenda": h.instances.items(i),
                          "lists": {n: h.instances.list_get(i, n) for n in h.instances.list_names(i)
                                    if i.main or n not in h.instances.shared_lists}})
        sats = []
        if h.satellites:
            for room, m in h.satellites.modules.items():
                sats.append({"room": room, "online": h.satellites.online(room),
                             "speaker": bool(m["flags"] & 1), "muted": bool(m["flags"] & 2)})
        from .state import MODES

        extra: dict = {}
        if getattr(h, "cameras", None):
            extra["cameras"] = [{"name": n, "online": f.online(), "roles": sorted(f.roles)}
                                for n, f in h.cameras.feeds.items()]
        if getattr(h, "weather", None) and h.weather.data:
            extra["weather"] = {"today": h.weather.summary(0), "tomorrow": h.weather.summary(1),
                                "needs": h.weather.needs()}
        if getattr(h, "energy", None) and h.energy.plugs:
            t = h.energy.totals(7)
            extra["energy"] = {"kwh": round(t["kwh"], 1), "cost": round(t["cost"], 2), "currency": h.energy.currency,
                               "devices": {k: round(v, 1) for k, v in list(t["devices"].items())[:6]},
                               "people": {h.energy.person_name(k): round(v, 1) for k, v in t["people"].items()},
                               "plugs": {n: {"watts": round(p.power), "state": p.state, "on": p.on}
                                         for n, p in h.energy.plugs.items()}}
        if getattr(h, "packages", None):
            from .packages import STATUS_WORDS

            extra["packages"] = [{**i, "words": STATUS_WORDS.get(i["status"], i["status"])} for i in h.packages.items]
        extra["countdowns"] = h.instances.countdowns(h.instances.main)
        extra["freak"] = h.freak.status()
        if getattr(h, "sim", None):
            extra["sim"] = h.sim.status()
        if h.assistant is not None:
            extra["ai"] = {"mode": h.assistant.mode, "model": h.assistant.model}
        if getattr(h, "voice_id", None) is not None:
            extra["voices"] = {**h.voice_id.status(),
                               "names": [i.name for i in h.instances.all.values() if not i.main]}
        if getattr(h, "alarm", None):
            extra["alarm"] = h.alarm.state
        if getattr(h, "rooms", None):
            extra["rooms"] = h.rooms.snapshot()
            extra["sleep"] = {p: (v[-1] if v else None) for p, v in h.rooms.sleep_log.items()}
        if getattr(h, "pets", None):
            extra["pets"] = {p: h.pets.status(p) for p in h.pets.pets}
        if getattr(h, "diary", None):
            extra["diary"] = {"today": h.diary.read(), "days": h.diary.days()[:14]}
        extra["climate"] = climate(h)
        extra["floorplan"] = self.floorplan()
        return {**extra,
            "mode": h.state.mode, "modes": MODES, "people_home": h.state.home_people(),
            "presence": h.state.presence, "private": h.state.is_private(None),
            "doors": h.doors.states(), "routines": list(h.config.routines),
            "instances": insts, "satellites": sats, "api_unlock": self.api_unlock,
            "offline": sorted(h.sensors.offline) if h.sensors else [],
            "events": list(reversed(h.events.query(limit=40))),
            "assistant": h.assistant is not None,
        }

    # ---------- typed commands ("as if heard in a room") ----------
    def heard(self, text: str, room: str | None) -> dict:
        h = self.home
        ai = h.assistant
        if ai is not None:
            rest = ai.addressed(text, room)
            if rest:
                answer = ai.ask(rest, room)
                h.voice.speak(answer, room=room)
                if getattr(h, "history", None):
                    h.history.add(kind="ai", room=room, question=rest, answer=answer, via="typed")
                return {"assistant": answer}
        engine = getattr(h, "engine", None)
        if engine is not None:
            matcher = engine._lang(room).matcher
        else:
            from .understanding import Aliases

            key = (len(h.config.commands), len(Aliases(h.config).all()))
            if getattr(self, "_matcher_n", None) != key:
                from .matcher import Matcher
                from .understanding import candidates_with_aliases

                s = h.config.settings
                self._matcher = Matcher(candidates_with_aliases(h.config), s.match_mode,
                                        s.match_threshold, s.block_words)
                self._matcher_n = key
            matcher = self._matcher
        m = matcher.match(text)
        if not m:
            if getattr(h, "history", None):
                h.history.add(kind="none", room=room, heard=text, via="typed")
            return {"matched": None, "hint": "No command matches that. Start with the wake name to ask the AI."}
        m.room, m.via = room, "typed"
        if getattr(h, "history", None):
            h.history.add(kind="command", room=room, heard=text, matched=m.command.name, via="typed")
        h.submit(m)
        return {"matched": m.command.name, "slots": m.slots}

    # ---------- the History page ----------
    def history_get(self, query: str = "") -> dict:
        from .understanding import Aliases

        h = self.home
        hist = getattr(h, "history", None)
        return {"enabled": bool(hist and hist.enabled), "days": hist.days if hist else 0,
                "entries": hist.entries(query) if hist else [],
                "commands": sorted((c.name, c.phrases[0]) for c in h.config.commands),
                "aliases": Aliases(h.config).all()}

    def history_post(self, what: str, body: dict) -> dict:
        from .understanding import Aliases

        h = self.home
        if what == "teach":  # "what I said there means this command"
            heard = body.get("heard")
            if not heard and body.get("id") and getattr(h, "history", None):
                e = h.history.get(str(body["id"]))
                heard = (e or {}).get("heard")
            item = Aliases(h.config).add(str(heard or ""), str(body["command"]))
            self._reload_matchers()
            return {"alias": item}
        if what == "unteach":
            ok = Aliases(h.config).remove(str(body["heard"]))
            self._reload_matchers()
            return {"removed": ok}
        if what == "clear":
            if getattr(h, "history", None):
                h.history.clear()
            return {"cleared": True}
        raise KeyError(f"/api/history/{what}")

    def _reload_matchers(self) -> None:
        self._matcher_n = None
        engine = getattr(self.home, "engine", None)
        if engine is not None:
            engine.reload()

    # ---------- floor plan ----------
    def _fp_path(self) -> Path:
        c = self.home.config
        return Path(c.resolve_path(c.data_dir)) / "floorplan.json"

    def floorplan(self) -> dict:
        """Room boxes (x, y, w, h in a 100x60 grid): config `floorplan:`, edits from the dashboard,
        and an automatic layout for any other known room."""
        fp = dict((self.home.config.extra.get("floorplan") or {}).get("rooms") or {})
        try:
            fp.update(json.loads(self._fp_path().read_text()))
        except (OSError, ValueError):
            pass
        rooms = {r for i in self.home.instances.all.values() for r in i.rooms}
        if getattr(self.home, "rooms", None):
            rooms |= set(self.home.rooms.occupancy_inputs) | set(self.home.rooms.follow)
        missing = sorted(r for r in rooms if r not in fp)
        for i, r in enumerate(missing):
            fp[r] = [2 + (i % 4) * 24.5, 2 + (i // 4) * 19, 23, 17]
        return fp

    def save_floorplan(self, rooms: dict) -> dict:
        clean = {str(k).lower(): [round(float(x), 1) for x in v][:4] for k, v in rooms.items() if len(v) >= 4}
        p = self._fp_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(clean))
        return clean

    # ---------- meals ----------
    def meals_get(self, week: str | None) -> dict:
        from .meals import DAYS, week_of

        m = self.home.meals
        wk = week or week_of()
        w = m.week(wk)
        return {"week": wk, "days": DAYS, "slots": m.slots, "plan": w["days"],
                "ingredients": m.ingredients(wk), "pantry": m.pantry(),
                "shopping": self.home.instances.list_get(self.home.instances.main, "shopping"),
                "diets": m.diets(), "ai": self.home.assistant is not None or bool(self.home.config.assistant)}

    def meals_post(self, what: str, body: dict) -> dict:
        m = self.home.meals
        wk = body.get("week")
        if what == "set":
            meal = body.get("meal")
            if meal and isinstance(meal.get("ingredients"), str):
                meal["ingredients"] = [x for x in meal["ingredients"].splitlines() if x.strip()]
            m.set_meal(body["day"], body["slot"], meal, wk)
        elif what == "plan":
            m.plan_week(body.get("request", ""), wk, keep_existing=not body.get("replace"))
        elif what == "suggest":
            return {"ideas": m.suggest(body.get("request", ""))}
        elif what == "sync":
            m.sync(wk)
        elif what == "pantry":
            insts = self.home.instances
            if body.get("add"):
                insts.list_add(insts.main, "pantry", list(body["add"]))
            if body.get("remove"):
                insts.list_remove(insts.main, "pantry", list(body["remove"]))
            if m.cfg.get("auto_sync", True):
                m.sync(wk)
        else:
            raise KeyError(f"/api/meals/{what}")
        return self.meals_get(wk)

    # ---------- the alarms page ----------
    def alarms_get(self) -> dict:
        from datetime import datetime

        from .wake import EDITABLE

        h = self.home
        out, insts = [], []
        for inst in h.instances.all.values():
            sensor = "bed" if h.wake.bed_person(inst) else ("motion" if h.wake.motion else "none")
            insts.append({"id": inst.id, "name": inst.name, "rooms": inst.rooms, "main": inst.main, "sensor": sensor})
            for it in h.instances.items(inst, ("alarm",)):
                due = datetime.fromisoformat(it["due"])
                out.append({"instance": inst.id, "name": inst.name, "id": it["id"], "time": due.strftime("%H:%M"),
                            "due": it["due"], "next": due.strftime("%a %b %d, %I:%M %p").replace(" 0", " "),
                            "repeat": it["repeat"], "label": it.get("label") or "", "room": it.get("room"),
                            "off": bool(it.get("off")), "snoozed": bool(it.get("snoozed")),
                            "wake": h.wake.settings(it), "sensor": sensor})
        out.sort(key=lambda a: (a["off"], a["due"]))
        return {"alarms": out, "sessions": h.wake.status(), "recent": h.wake.recent(),
                "instances": insts, "defaults": {k: h.wake.defaults[k] for k in EDITABLE}}

    def alarms_post(self, what: str, body: dict) -> dict:
        from .instances import Instances
        from .wake import EDITABLE

        h = self.home
        if what in ("stop", "snooze"):  # the same as saying it in that room
            return {"result": h.run_action({"backend": "alarm", "alarm": what}, {"here": body.get("room")})}
        inst = h.instances.all.get(str(body.get("instance", "")).lower())
        if inst is None:
            raise KeyError(f"no one called {body.get('instance')!r}")
        if what == "delete":
            return {"deleted": len(h.instances.cancel(inst, str(body["id"]), kinds=("alarm",)))}
        if what == "ring":  # test it: a one-off copy that goes off right away
            item = next(i for i in h.instances.items(inst, ("alarm",)) if i["id"] == body["id"])
            from . import clock

            new = h.instances.add(inst, "alarm", clock.now(), label=item.get("label") or "test", room=item.get("room"))
            if item.get("wake"):
                h.instances.update_item(inst, new["id"], wake=dict(item["wake"]))
            return {"ok": True}
        if what != "save":
            raise KeyError(f"/api/alarms/{what}")
        limits = {"recheck_minutes": (0.5, 30), "snooze_limit": (0, 10), "snooze_minutes": (1, 30),
                  "up_seconds": (5, 600), "back_to_bed_minutes": (0, 60), "give_up_minutes": (5, 180)}
        wake_in = body.get("wake") or {}
        wake = {}
        for k in EDITABLE:
            if k not in wake_in:
                continue
            if k == "make_sure_up":
                wake[k] = bool(wake_in[k])
            else:
                lo, hi = limits[k]
                wake[k] = min(hi, max(lo, float(wake_in[k])))
                if k == "snooze_limit":
                    wake[k] = int(wake[k])
        fields = {k: body[k] for k in ("time", "repeat", "label", "room") if body.get(k) is not None}
        if "off" in body:
            fields["off"] = bool(body["off"])
        if body.get("id"):
            old = next((i for i in h.instances.items(inst, ("alarm",)) if i["id"] == body["id"]), None)
            if old is None:
                raise KeyError("that alarm is gone")
            if wake:
                fields["wake"] = {**(old.get("wake") or {}), **wake}
            item = h.instances.update_item(inst, body["id"], **fields)
        else:
            repeat = str(fields.get("repeat", "none"))
            item = h.instances.add(inst, "alarm", Instances.next_time(str(fields.get("time", "07:00")), repeat),
                                   label=str(fields.get("label", "")), repeat=repeat, room=fields.get("room"))
            if wake:
                item = h.instances.update_item(inst, item["id"], wake=wake)
        return {"item": item}

    def handle_post(self, path: str, body: dict) -> dict:
        h = self.home
        if path == "/api/routine":
            return {"result": h.run_action({"backend": "routine", "routine": body["name"]})}
        if path == "/api/command":
            cmd = next(c for c in h.config.commands if c.name == body["name"])
            values = dict(body.get("slots") or {})
            if body.get("room"):
                values["here"] = body["room"]
            return {"result": h.run_action(cmd.action, values, f"web: {cmd.name}")}
        if path == "/api/door":
            if str(body.get("state")).startswith("unlock") and not self.api_unlock:
                raise PermissionError("unlocking from the dashboard is off (web.api_unlock)")
            return {"result": h.doors.set(body["door"], body.get("state", "locked"), reason="dashboard")}
        if path == "/api/mode":
            return {"result": h.state.set_mode(body["mode"], reason="dashboard")}
        if path == "/api/privacy":
            return {"result": h.state.set_privacy(bool(body.get("on", True)), body.get("minutes"), None)}
        if path == "/api/say":
            rooms = body.get("rooms") or ([body["room"]] if body.get("room") else None)
            h.voice.speak("intercom", rooms=rooms, everywhere=bool(body.get("everywhere")),
                          text=str(body["text"]))
            return {"result": "said"}
        if path == "/api/heard":
            return self.heard(str(body.get("text", "")), body.get("room") or None)
        if path == "/api/ask":
            if h.assistant is None:
                raise RuntimeError("the AI assistant isn't enabled")
            answer = h.assistant.ask(str(body["question"]), room=body.get("room"))
            if body.get("speak"):
                h.voice.speak(answer, room=body.get("room"))
            return {"answer": answer}
        if path == "/api/list":
            inst = h.instances.get(body.get("person"), asker=h.instances.main)
            name = body.get("list", "shopping")
            if body.get("add"):
                h.instances.list_add(inst, name, list(body["add"]))
            if body.get("remove"):
                h.instances.list_remove(inst, name, list(body["remove"]))
            return {"list": h.instances.list_get(inst, name)}
        if path == "/api/agenda/cancel":
            inst = h.instances.get(body.get("person"), asker=h.instances.main)
            return {"cancelled": len(h.instances.cancel(inst, body["id"]))}
        if path in ("/api/voice/learn", "/api/voice/forget"):  # who's talking
            if getattr(h, "voice_id", None) is None:
                raise RuntimeError("voice recognition is off (voice_id: in config.yaml)")
            if path.endswith("learn"):
                h.voice_id.start_learning(str(body["person"]), int(body.get("sentences", 6)))
            else:
                h.voice_id.forget(str(body["person"]))
            return {"voices": h.voice_id.status()}
        if path == "/api/ai":  # the smart / fast switch
            if h.assistant is None:
                raise RuntimeError("the AI isn't on")
            return {"mode": h.assistant.set_mode(body.get("mode", "smart")), "model": h.assistant.model}
        if path == "/api/sim":  # simulate: move the laptop's mic / webcam
            if not getattr(h, "sim", None):
                raise RuntimeError("only while simulating")
            if body.get("mic_room"):
                h.sim.move_mic(body["mic_room"])
            if "camera" in body:
                h.sim.move_camera(body["camera"] or None)
            return {"sim": h.sim.status()}
        if path == "/api/freak":
            if body.get("action", "off") == "off":
                return {"result": h.freak.stop(body.get("room"))}
            return {"result": h.freak.start(body.get("room"), body.get("minutes"))}
        if path == "/api/alarm":
            return {"result": h.run_action({"backend": "security", "security": body["action"]})}
        if path == "/api/package":
            if not getattr(h, "packages", None):
                raise RuntimeError("package tracking isn't set up")
            if body.get("action") == "remove":
                return {"removed": h.packages.remove(body["number"])}
            return {"item": h.packages.add(body["number"], body.get("name", ""))}
        if path == "/api/countdown":
            inst = h.instances.get(body.get("person"), asker=h.instances.main)
            if body.get("action") == "remove":
                return {"removed": h.instances.countdown_remove(inst, body["name"])}
            return {"item": h.instances.countdown_add(inst, body["name"], body["date"])}
        if path == "/api/list/bought":
            if getattr(h, "meals", None):
                h.meals.bought(body["item"])
            else:
                h.instances.list_remove(h.instances.main, "shopping", [body["item"]])
            return {"ok": True}
        if path == "/api/floorplan":
            return {"floorplan": self.save_floorplan(body.get("rooms") or {})}
        if path.startswith("/api/meals/"):
            return self.meals_post(path.rsplit("/", 1)[1], body)
        if path.startswith("/api/alarms/"):
            return self.alarms_post(path.rsplit("/", 1)[1], body)
        if path.startswith("/api/history/"):
            return self.history_post(path.rsplit("/", 1)[1], body)
        if path == "/api/event":
            kind = "".join(ch for ch in str(body.get("kind", "event")) if ch.isalnum() or ch in "_-")
            data = {k: v for k, v in body.items() if k not in ("kind", "token") and isinstance(v, (str, int, float, bool))}
            h.events.log(f"external.{kind}", **data)
            if "battery" in data and h.sensors:
                h.sensors.battery(kind, float(data["battery"]))
            return {"result": "ok"}
        raise KeyError(path)


class _Handler(BaseHTTPRequestHandler):
    web: WebServer

    def log_message(self, *a) -> None:
        pass

    def _authorized(self, query: dict) -> bool:
        tok = self.web.token
        if not tok:
            return True
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        given = (self.headers.get("X-Token") or query.get("token", [""])[0]
                 or (cookie["ph_token"].value if "ph_token" in cookie else ""))
        return given == tok

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else (
            body.encode() if isinstance(body, str) else json.dumps(body, default=str).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, DASHBOARD, "text/html; charset=utf-8")
        if u.path == "/meals":
            from .dashboard_meals import MEALS_PAGE

            return self._send(200, MEALS_PAGE, "text/html; charset=utf-8")
        if u.path == "/alarms":
            from .dashboard_alarms import ALARMS_PAGE

            return self._send(200, ALARMS_PAGE, "text/html; charset=utf-8")
        if u.path == "/history":
            from .dashboard_history import HISTORY_PAGE

            return self._send(200, HISTORY_PAGE, "text/html; charset=utf-8")
        if not self._authorized(q):
            return self._send(401, {"error": "token required"})
        if u.path == "/api/status":
            return self._send(200, self.web.status())
        if u.path == "/api/live":
            return self._live()
        if u.path == "/api/alarms":
            return self._send(200, self.web.alarms_get())
        if u.path == "/api/history":
            return self._send(200, self.web.history_get(q.get("q", [""])[0]))
        if u.path == "/api/meals":
            if not getattr(self.web.home, "meals", None):
                return self._send(404, {"error": "meal planner isn't running"})
            return self._send(200, self.web.meals_get(q.get("week", [None])[0]))
        if u.path in ("/api/camera", "/api/camera/stream"):
            cams = getattr(self.web.home, "cameras", None)
            feed = cams.get(q.get("name", [""])[0]) if cams else None
            if feed is None:
                return self._send(404, {"error": "no such camera"})
            if u.path == "/api/camera":
                return self._send(200, feed.jpeg or b"", "image/jpeg")
            return self._mjpeg(feed)
        if u.path == "/api/photo":
            c = self.web.home.config
            root = (Path(c.resolve_path(c.data_dir)) / "faces").resolve()
            f = Path(q.get("f", [""])[0]).resolve()
            if root in f.parents and f.suffix == ".jpg" and f.is_file():
                return self._send(200, f.read_bytes(), "image/jpeg")
            return self._send(404, {"error": "not found"})
        self._send(404, {"error": "not found"})

    def _live(self) -> None:
        """Server-sent events: every house event the moment it happens, plus live-only updates
        (heard / thinking / searching / saying). The dashboard refreshes on these instead of waiting."""
        import queue as _queue
        import time as _t

        q: _queue.Queue = _queue.Queue(maxsize=500)

        def on_event(ev: dict) -> None:
            try:
                q.put_nowait(ev)
            except _queue.Full:
                pass

        events = self.web.home.events
        events.subscribe(on_event)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            end = _t.monotonic() + 3600  # the browser reconnects by itself
            while _t.monotonic() < end:
                try:
                    ev = q.get(timeout=15)
                except _queue.Empty:
                    self.wfile.write(b": still here\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"data: " + json.dumps(ev, default=str).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            events.unsubscribe(on_event)

    def _mjpeg(self, feed) -> None:
        """Live view: a multipart JPEG stream (works in any browser <img>), for up to 10 minutes."""
        import time as _t

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        end, last = _t.monotonic() + 600, 0.0
        try:
            self.wfile.write(b"--frame\r\n")
            while _t.monotonic() < end:
                _, jpeg, t = feed.latest()
                if jpeg and t != last:
                    last = t
                    # the boundary right after each picture tells the browser it's complete
                    self.wfile.write(b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                                     + b"\r\n\r\n" + jpeg + b"\r\n--frame\r\n")
                _t.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        u = urlparse(self.path)
        if not self._authorized(parse_qs(u.query)):
            return self._send(401, {"error": "token required"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
            self._send(200, self.web.handle_post(u.path, body))
        except KeyError as e:
            self._send(404 if str(e).strip("'").startswith("/") else 400, {"error": f"missing {e}"})
        except StopIteration:
            self._send(404, {"error": "no such command"})
        except PermissionError as e:
            self._send(403, {"error": str(e)})
        except Exception as e:
            log.warning("web %s failed: %s", u.path, e)
            self._send(400, {"error": str(e)})


from .dashboard_html import DASHBOARD  # noqa: E402
