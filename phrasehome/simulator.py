"""A virtual house for testing before you have the place (or any hardware).

    phrasehome -c config.yaml simulate            # simulated devices + dashboard, type commands
    phrasehome -c config.yaml simulate --mic      # also listen on the laptop's microphone
    phrasehome -c config.yaml simulate --webcam   # the laptop webcam stands in for the door camera

Every ESP32 device node in your config becomes a simulated node on this computer, with the
channels and inputs your config uses. Each has a page (http://localhost:9100, 9101, ...) where you
see lights and locks switch, and flip sensors (open a door, trigger motion, set the temperature,
put the keys tag in a room). Online backends (Google, phone alerts, Home Assistant, webhooks,
speakers) are replaced by a log, so nothing real is touched, unless you add --real-backends.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

log = logging.getLogger("phrasehome.sim")

ANALOG_HINTS = ("temp", "humid", "soil", "water", "light", "distance", "level", "battery", "moisture")


def scan_usage(data: Any, nodes: list[str]) -> dict[str, dict[str, dict[str, str]]]:
    """Which channels and inputs the config uses on each node: {node: {"channels": {..}, "inputs": {..}}}"""
    usage = {n: {"channels": {}, "inputs": {}} for n in nodes}

    def add(node: str, kind: str, name: str, analog: bool = False) -> None:
        targets = nodes if ("{" in node or node == "all") else [node]
        for t in targets:
            if t in usage:
                if kind == "inputs":
                    old = usage[t]["inputs"].get(name)
                    usage[t]["inputs"][name] = "analog" if analog or old == "analog" else "digital"
                else:
                    usage[t]["channels"][name] = "dimmable" if analog else usage[t]["channels"].get(name, "switch")

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            node = x.get("node")
            if isinstance(node, str):
                node = node.lower()
                if isinstance(x.get("channel"), str):
                    add(node, "channels", x["channel"].lower(), "level" in x)
                if isinstance(x.get("input"), str):
                    name = x["input"].lower()
                    add(node, "inputs", name, "below" in x or "above" in x or name.startswith(ANALOG_HINTS))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data)
    return usage


class SimNode:
    """Speaks the same HTTP API as firmware/device_node, plus a page to play with it."""

    def __init__(self, name: str, port: int, channels: dict[str, str], inputs: dict[str, str],
                 tags: dict[str, str] | None = None) -> None:
        self.name = name
        self.channels = {c: {"on": False, "level": 100, "kind": k} for c, k in channels.items()}
        self.inputs: dict[str, dict[str, Any]] = {}
        for i, k in inputs.items():
            if k == "analog":
                start = 21.0 if i.startswith("temp") else 45 if i.startswith("humid") else 60
                self.inputs[i] = {"value": start}
            else:
                # contacts and locks start "closed" (on) so doors aren't reported open
                self.inputs[i] = {"on": any(w in i for w in ("contact", "closed", "window", "door")), "count": 0}
        self.tags = tags or {}           # tag name -> mac
        self.here: set[str] = set()      # tags currently "in" this node's room
        self.ir_log: list[str] = []
        self._timers: dict[str, threading.Timer] = {}
        node = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/api/status":
                    return self._send(200, node.status())
                if u.path == "/api/ir/learn":
                    return self._send(200, {"seq": len(node.ir_log)})
                return self._send(200, PAGE.replace("{{NAME}}", node.name), "text/html; charset=utf-8")

            def do_POST(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                try:
                    if u.path == "/api/set":
                        return self._send(200, node.set(q))
                    if u.path == "/api/input":
                        return self._send(200, node.set_input(q))
                    if u.path == "/api/tag":
                        (node.here.add if q.get("here") == "1" else node.here.discard)(q["tag"])
                        return self._send(200, node.status())
                    if u.path == "/api/ir/send":
                        node.ir_log.append(f"{time.strftime('%H:%M:%S')} {q.get('protocol')} {q.get('code')}")
                        log.info("[sim %s] IR %s %s", node.name, q.get("protocol"), q.get("code"))
                        return self._send(200, {"sent": True})
                    return self._send(404, {"error": "not found"})
                except KeyError as e:
                    return self._send(404, {"error": f"no such {e}"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def status(self) -> dict:
        chans = [{"name": n, "on": c["on"], **({"level": c["level"]} if c["kind"] == "dimmable" else {})}
                 for n, c in self.channels.items()]
        inputs = [{"name": n, **v} for n, v in self.inputs.items()]
        out = {"node": self.name, "channels": chans, "inputs": inputs, "ir": self.ir_log[-10:]}
        if self.tags:
            out["ble"] = [{"mac": self.tags[t], "rssi": -55} for t in self.here if t in self.tags]
            out["tags"] = {t: t in self.here for t in self.tags}
        return out

    def set(self, q: dict) -> dict:
        ch = self.channels[q["channel"].lower()]
        st = q.get("state", "").lower()
        if st in ("on", "true", "open"):
            ch["on"] = True
        elif st in ("off", "false", "close"):
            ch["on"] = False
        elif st == "toggle":
            ch["on"] = not ch["on"]
        if q.get("level"):
            ch["level"] = max(0, min(100, int(float(q["level"]))))
            if not st:
                ch["on"] = ch["level"] > 0
        name = q["channel"].lower()
        log.info("[sim %s] %s -> %s%s", self.name, name, "on" if ch["on"] else "off",
                 f" {ch['level']}%" if q.get("level") else "")
        if self._timers.get(name):
            self._timers.pop(name).cancel()
        if q.get("for"):
            t = threading.Timer(float(q["for"]), lambda: ch.update(on=not ch["on"]))
            t.daemon = True
            t.start()
            self._timers[name] = t
        return self.status()

    def set_input(self, q: dict) -> dict:
        inp = self.inputs[q["name"].lower()]
        if "value" in q:
            inp["value"] = float(q["value"])
        elif "press" in q:
            inp["count"] = inp.get("count", 0) + 1
        else:
            on = q.get("on", "toggle")
            new = (not inp.get("on")) if on == "toggle" else on in ("1", "true", "on")
            if new and not inp.get("on"):
                inp["count"] = inp.get("count", 0) + 1
            inp["on"] = new
        return self.status()

    def close(self) -> None:
        self.httpd.shutdown()


PAGE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>sim: {{NAME}}</title>
<style>body{font:15px system-ui;max-width:560px;margin:24px auto;padding:0 12px;color:#222}h1{font-size:20px}
h2{font-size:12px;text-transform:uppercase;color:#777;margin:20px 0 6px}.r{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid #eee}
.on{background:#ffd75e}.dot{display:inline-block;width:14px;height:14px;border-radius:50%;border:1px solid #999;vertical-align:middle;margin-right:8px}
button{font:inherit;padding:4px 10px;border-radius:6px;border:1px solid #bbb;background:#fff;cursor:pointer}input[type=range]{width:160px}.m{color:#888;font-size:13px}</style>
<h1>Simulated device node: {{NAME}}</h1><div class=m>What the hub switches shows up here. Flip the sensors to test automations.</div>
<h2>Outputs</h2><div id=ch></div><h2>Sensors</h2><div id=inp></div><div id=tagsBox><h2>Bluetooth tags in this room</h2><div id=tags></div></div><h2>IR sent</h2><div id=ir class=m></div>
<script>
const A=[],b=(l,f)=>{A.push(f);return `<button onclick="A[${A.length-1}]()">${l}</button>`};
const e=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const P=(u)=>fetch(u,{method:'POST'}).then(load);
async function load(){const s=await (await fetch('/api/status')).json();A.length=0;
 ch.innerHTML=s.channels.map(c=>`<div class=r><span><span class="dot ${c.on?'on':''}"></span>${e(c.name)} ${c.level!=null?`<span class=m>${c.level}%</span>`:''}</span><span class=m>${c.on?'ON':'off'}</span></div>`).join('')||'<span class=m>none used</span>';
 inp.innerHTML=s.inputs.map(i=>'value' in i?`<div class=r><span>${e(i.name)}</span><span><input type=range min=0 max=100 step=0.5 value="${i.value}" onchange="P('/api/input?name='+encodeURIComponent('${e(i.name)}')+'&value='+this.value)"> <b>${i.value}</b></span></div>`
  :`<div class=r><span><span class="dot ${i.on?'on':''}"></span>${e(i.name)} <span class=m>${i.on?'on':'off'} · ${i.count} times</span></span><span>${b('toggle',()=>P('/api/input?name='+encodeURIComponent(i.name)))} ${b('press',()=>P('/api/input?name='+encodeURIComponent(i.name)+'&press=1'))}</span></div>`).join('')||'<span class=m>none used</span>';
 tagsBox.style.display=s.tags?'':'none';if(s.tags)tags.innerHTML=Object.entries(s.tags).map(([t,h])=>`<div class=r><span>${e(t)}</span>${b(h?'take it away':'put it here',()=>P('/api/tag?tag='+encodeURIComponent(t)+'&here='+(h?0:1)))}</div>`).join('');
 ir.innerHTML=(s.ir||[]).map(e).join('<br>')||'nothing yet'}
load();setInterval(load,1000);
</script>"""


SAFE_TYPES = {"esp32", "log"}


def prepare(config, base_port: int = 9100, real_backends: bool = False, webcam: bool = False):
    """Start simulated nodes for the config and point the config at them. Returns the nodes."""
    raw = yaml.safe_load(config.base_dir.joinpath(config.source_name).read_text()) if \
        getattr(config, "source_name", None) else {}
    nodes_by_backend = {n: b for n, b in config.backends.items() if b["type"] == "esp32"}
    all_nodes = sorted({str(k).lower() for b in nodes_by_backend.values() for k in (b.get("nodes") or {})})
    usage = scan_usage(raw or {}, all_nodes)
    tags = {t: v.get("mac", "") for t, v in (config.extra.get("tags") or {}).items() if v.get("mac")}
    sims: dict[str, SimNode] = {}
    for i, node in enumerate(all_nodes):
        u = usage.get(node, {"channels": {}, "inputs": {}})
        sims[node] = SimNode(node, base_port + i if base_port else 0, u["channels"], u["inputs"], tags)
    for b in nodes_by_backend.values():
        b["nodes"] = {k: f"127.0.0.1:{sims[str(k).lower()].port}" for k in (b.get("nodes") or {})}
        b.pop("token", None)
        b.pop("token_env", None)
    if not real_backends:
        for name, b in list(config.backends.items()):
            if b["type"] not in SAFE_TYPES:
                config.backends[name] = {"type": "log"}
        config.fingerprint = []
    if webcam:
        door = next((n for n, c in config.cameras.items() if "door" in (c.get("roles") or [])), None)
        for n in list(config.cameras):
            if n == door:
                config.cameras[n] = {**config.cameras[n], "url": 0}
            else:
                config.cameras.pop(n)
    else:
        config.cameras = {}
    config.door_scanners = [dict(sc, camera=next(iter(config.cameras))) for sc in config.door_scanners] \
        if config.cameras else []
    config.web = {**config.web, "enabled": True}
    config.web.pop("token_env", None)
    return sims


def run(config, mic: bool = False, base_port: int = 9100, real_backends: bool = False,
        webcam: bool = False) -> None:
    sims = prepare(config, base_port, real_backends, webcam)
    print("\nSimulated device nodes (open them to see lights switch and to flip sensors):")
    for name, s in sims.items():
        print(f"  {name:<14} http://localhost:{s.port}")
    print(f"Dashboard: http://localhost:{config.web.get('port', 8800)}  (type commands in 'Say or ask')")
    if not real_backends:
        print("Online backends (Google, phone alerts, speakers...) are replaced by the log.")
    print()
    if mic:
        from .listener import listen

        listen(config)
        return
    from .dispatcher import Dispatcher
    from .services import start_services

    home = Dispatcher(config)
    if config.assistant:
        try:
            from .assistant import Assistant

            home.assistant = Assistant(home)
        except Exception as e:
            log.warning("AI assistant off: %s", e)
    scanners = []
    for sc in config.door_scanners:
        try:
            from .door_scanner import DoorScanner

            scanners.append(DoorScanner(home, sc))
            scanners[-1].start()
        except Exception as e:
            log.warning("door scanner off: %s", e)
    services = start_services(home)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for s in services + scanners:
            s.stop()
        home.close()
        for s in sims.values():
            s.close()
