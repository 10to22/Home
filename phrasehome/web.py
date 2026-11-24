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

        return {
            "mode": h.state.mode, "modes": MODES, "people_home": h.state.home_people(),
            "presence": h.state.presence, "private": h.state.is_private(None),
            "doors": h.doors.states(), "routines": list(h.config.routines),
            "instances": insts, "satellites": sats, "api_unlock": self.api_unlock,
            "offline": sorted(h.sensors.offline) if h.sensors else [],
            "events": list(reversed(h.events.query(limit=40))),
            "assistant": h.assistant is not None,
        }

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
        if path == "/api/event":
            kind = "".join(ch for ch in str(body.get("kind", "event")) if ch.isalnum() or ch in "_-")
            data = {k: v for k, v in body.items() if k != "kind" and isinstance(v, (str, int, float, bool))}
            h.events.log(f"external.{kind}", **data)
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
        if not self._authorized(q):
            return self._send(401, {"error": "token required"})
        if u.path == "/api/status":
            return self._send(200, self.web.status())
        if u.path == "/api/photo":
            c = self.web.home.config
            root = (Path(c.resolve_path(c.data_dir)) / "faces").resolve()
            f = Path(q.get("f", [""])[0]).resolve()
            if root in f.parents and f.suffix == ".jpg" and f.is_file():
                return self._send(200, f.read_bytes(), "image/jpeg")
            return self._send(404, {"error": "not found"})
        self._send(404, {"error": "not found"})

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


DASHBOARD = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Home</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:16px 20px}
h1{font-size:20px;margin:0}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}.mute{color:var(--mute);font-size:13px}
button,select,input{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 10px}
button{cursor:pointer}button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
.pill{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.locked{color:var(--ok)}.unlocked,.bad{color:var(--warn)}
.chips{display:flex;flex-wrap:wrap;gap:6px}.x{border:0;background:none;color:var(--mute);padding:0 4px}
img.scan{width:44px;height:44px;object-fit:cover;border-radius:6px;vertical-align:middle;margin-right:8px}
form{display:flex;gap:6px;margin-top:8px}form input{flex:1;min-width:0}
#answer{margin-top:8px}
</style>
<header><h1>Home</h1><div class="chips">
<select id="mode" onchange="post('/api/mode',{mode:this.value})"></select>
<button id="priv" onclick="post('/api/privacy',{on:!S.private,minutes:60})"></button></div></header>
<main>
<section class="card"><h2>Doors</h2><div id="doors"></div></section>
<section class="card"><h2>Routines</h2><div class="chips" id="routines"></div></section>
<section class="card"><h2>Say or ask</h2>
<form onsubmit="say(event)"><input id="sayText" placeholder="Dinner's ready!"><select id="sayRoom"></select><button>Say</button></form>
<form id="askForm" onsubmit="ask(event)"><input id="askText" placeholder="Ask the house..."><button class="primary">Ask</button></form>
<div id="answer" class="mute"></div></section>
<section class="card"><h2>Who's home</h2><div id="people"></div></section>
<div id="rooms" style="display:contents"></div>
<section class="card"><h2>Devices</h2><div id="devices"></div></section>
<section class="card" style="grid-column:1/-1"><h2>Recent activity</h2><div id="events"></div></section>
</main>
<script>
let S={},A=[];const qs=new URLSearchParams(location.search);
// buttons call closures by index, so no data from the house is ever written into JS code
const btn=(label,fn,cls='')=>{A.push(fn);return `<button class="${cls}" onclick="A[${A.length-1}]()">${label}</button>`};
if(qs.get('token')){document.cookie='ph_token='+encodeURIComponent(qs.get('token'))+';max-age=31536000;path=/';history.replaceState(null,'','/')}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)alert(j.error||r.status);load();return j}
function say(e){e.preventDefault();const t=sayText.value.trim();if(!t)return;const room=sayRoom.value;post('/api/say',room?{text:t,room}:{text:t,everywhere:true});sayText.value=''}
async function ask(e){e.preventDefault();const q=askText.value.trim();if(!q)return;answer.textContent='Thinking...';const j=await post('/api/ask',{question:q});answer.textContent=j.answer||j.error||'';askText.value=''}
function addItem(e,f){e.preventDefault();const i=S.instances[+f.dataset.i],v=f.item.value.trim();if(v)post('/api/list',{person:i.id,list:f.list.value||(i.main?'shopping':'todo'),add:[v]});f.item.value=''}
function when(iso){const d=new Date(iso);return d.toLocaleString([], {weekday:'short',hour:'numeric',minute:'2-digit'})}
async function load(){
 const r=await fetch('/api/status');
 if(r.status==401){const t=prompt('Dashboard token');if(t){document.cookie='ph_token='+encodeURIComponent(t)+';max-age=31536000;path=/';load()}return}
 S=await r.json();A=[];
 mode.innerHTML=S.modes.map(m=>`<option ${m==S.mode?'selected':''}>${m}</option>`).join('');
 priv.textContent=S.private?'Mics paused - resume':'Pause mics 1h';
 doors.innerHTML=Object.entries(S.doors).map(([d,s])=>`<div class="row"><span>${esc(d)} <span class="pill ${s}">${s}</span></span><span>
  ${btn('Lock',()=>post('/api/door',{door:d,state:'locked'}))}
  ${S.api_unlock?btn('Unlock',()=>post('/api/door',{door:d,state:'unlocked'})):''}</span></div>`).join('')||'<span class=mute>No doors configured</span>';
 routines.innerHTML=S.routines.map(n=>btn(esc(n.replace(/_/g,' ')),()=>post('/api/routine',{name:n}))).join('')||'<span class=mute>None yet</span>';
 const rooms=[...new Set(S.instances.flatMap(i=>i.rooms))];
 sayRoom.innerHTML='<option value="">Everywhere</option>'+rooms.map(r=>`<option>${esc(r)}</option>`).join('');
 askForm.style.display=S.assistant?'flex':'none';
 people.innerHTML=Object.entries(S.presence).map(([p,v])=>`<div class="row"><span>${esc(p)}</span><span class="pill ${v.state=='home'?'locked':''}">${v.state} since ${esc(v.since.slice(5,16))}</span></div>`).join('')||'<span class=mute>No presence info yet</span>';
 document.getElementById('rooms').innerHTML=S.instances.map(i=>`<section class="card"><h2>${esc(i.name)}${i.main?' (main)':''}${i.dnd?' · do not disturb':''}${i.quiet?' · quiet hours':''}</h2>
  ${i.agenda.length?i.agenda.map(a=>`<div class="row"><span>${a.kind=='alarm'?'⏰':a.kind=='timer'?'⏱':a.kind=='action'?'⚙':'🔔'} ${esc(a.text||a.label||a.kind)}${a.repeat!='none'?` <span class=mute>(${esc(a.repeat)})</span>`:''}</span><span class=mute>${when(a.due)} ${btn('✕',()=>post('/api/agenda/cancel',{person:i.id,id:a.id}),'x')}</span></div>`).join(''):'<div class=mute>No reminders</div>'}
  ${Object.entries(i.lists).map(([n,items])=>`<div style="margin-top:10px"><b>${esc(n)}</b><div class=chips style="margin-top:4px">${items.map(it=>`<span class=pill>${esc(it)} ${btn('✕',()=>post('/api/list',{person:i.id,list:n,remove:[it]}),'x')}</span>`).join('')||'<span class=mute>empty</span>'}</div></div>`).join('')}
  <form data-i="${S.instances.indexOf(i)}" onsubmit="addItem(event,this)">
  <input name="item" placeholder="Add to list"><input name="list" placeholder="${i.main?'shopping':'todo'}" style="max-width:90px"><button>Add</button></form></section>`).join('');
 const devs=S.satellites.map(s=>`<div class="row"><span>mic: ${esc(s.room)}</span><span class="pill ${s.online?'':'bad'}">${s.online?(s.muted?'muted':'online'):'offline'}${s.speaker?' · speaker':''}</span></div>`);
 devices.innerHTML=(S.offline.length?`<div class="row bad">Offline: ${S.offline.map(esc).join(', ')}</div>`:'')+devs.join('')||'<span class=mute>All good</span>';
 events.innerHTML=S.events.map(e=>`<div class="row"><span>${e.photo?`<img class=scan src="/api/photo?f=${encodeURIComponent(e.photo)}">`:''}<b>${esc(e.kind)}</b> ${esc(e.name||e.result||e.door||e.node||e.person||e.mode||e.text||e.question||'')} ${e.result&&e.name?esc(e.result):''}</span><span class=mute>${esc(e.time.slice(5,16))}</span></div>`).join('');
}
load();setInterval(load,5000);
</script></html>"""
