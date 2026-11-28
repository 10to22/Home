"""What's broken, and how to fix it: `phrasehome doctor`, or the dashboard's /health page.

Checks the speech model, Whisper (or the hearing server), ffmpeg, the voice, the AI, phones, the ESP32
modules and cameras, disk space, updates, and the last errors in the log, each with the fix in plain words.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("phrasehome")


class RecentProblems(logging.Handler):
    """Keeps the last warnings and errors for the health page."""

    def __init__(self, keep: int = 60) -> None:
        super().__init__(level=logging.WARNING)
        self.items: deque = deque(maxlen=keep)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.items.append({"time": time.strftime("%H:%M:%S", time.localtime(record.created)),
                               "level": record.levelname.lower(), "where": record.name.replace("phrasehome.", ""),
                               "message": record.getMessage()[:300]})
        except Exception:
            pass


_RECENT: RecentProblems | None = None


def watch_log() -> RecentProblems:
    global _RECENT
    if _RECENT is None:
        _RECENT = RecentProblems()
        logging.getLogger().addHandler(_RECENT)
    return _RECENT


def _row(name: str, ok: bool | None, detail: str, fix: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def node_problems(config) -> list[str]:
    """Steps and sensors in config.yaml that name an ESP32 module the esp32 backend doesn't list (a typo,
    or one not added yet): "windows.lounge window: node 'living' isn't one of: kitchen, bedroom, ..."."""
    esp = {n for n, b in config.backends.items() if b.get("type") == "esp32"}
    known = {str(k).lower() for n in esp for k in (config.backends[n].get("nodes") or {})}
    if not known:
        return []
    default = config.settings.default_backend
    out: list[str] = []

    def walk(x, where: str) -> None:
        if isinstance(x, dict):
            node = x.get("node")
            backend = x.get("backend", default)
            if isinstance(node, str) and "{" not in node and node.lower() not in known | {"all"} \
                    and (backend is None or backend in esp):
                out.append(f"{where}: node '{node}' isn't one of: {', '.join(sorted(known))}")
            for k, v in x.items():
                if k not in ("node", "_context"):
                    walk(v, f"{where}.{k}" if where else str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, f"{where}[{i}]")

    walk({c.name: c.action for c in config.commands}, "commands")
    for name in ("routines", "schedules", "automations", "sensors", "presence", "doors", "vacation", "extra"):
        val = getattr(config, name, None)
        if val:
            walk(val, "" if name == "extra" else name)
    return list(dict.fromkeys(out))


def checks(home, online: bool = True) -> list[dict]:
    c = home.config
    s = c.settings
    out: list[dict] = []
    # ---- hearing
    rec = str(s.recognizer).lower()
    model = Path(c.resolve_path(s.model_path))
    vosk_ok = (model / "am").exists() or (model / "graph").exists()
    out.append(_row("Speech model (Vosk)", vosk_ok or rec == "whisper",
                    f"{model.name} {'found' if vosk_ok else 'missing'}" + (" (not used: Whisper hears)" if rec == "whisper" else ""),
                    "Download it: phrasehome download-models"))
    try:
        import vosk  # noqa: F401

        out.append(_row("Vosk installed", True, "yes"))
    except ImportError:
        out.append(_row("Vosk installed", rec == "whisper", "no", "pip install vosk (or use recognizer: whisper)"))
    server = str(getattr(s, "whisper_server", "") or "")
    second = str(getattr(s, "second_opinion", "auto") or "")
    for label, url in (("Hearing server", server), ("Second opinion server", second if second.startswith("http") else "")):
        if url and online:
            from .hearing_server import RemoteWhisper

            w = RemoteWhisper(url, str(c.web.get("token", "") or ""))
            up = w.available()
            out.append(_row(label, up, f"{url}: {'answering, ' + w.name if up else 'not answering'}",
                            f"On that laptop run: phrasehome hearing-server (and keep it awake)"))
    if rec == "whisper" and not server or (second == "auto" and sys.platform == "darwin"):
        try:
            import faster_whisper  # noqa: F401

            out.append(_row("Whisper installed", True, "yes" + (" (double-checks near misses)" if rec != "whisper" else "")))
        except ImportError:
            out.append(_row("Whisper installed", rec != "whisper", "no",
                            "Much better hearing: .venv/bin/pip install faster-whisper, then settings: {recognizer: whisper}"))
    elif rec != "whisper" and sys.platform == "darwin":
        out.append(_row("Hearing", None, "Vosk", "Better: pip install faster-whisper and settings: {recognizer: whisper}"))
    # ---- sound
    out.append(_row("ffmpeg", bool(shutil.which("ffmpeg")), "found" if shutil.which("ffmpeg") else "missing",
                    "Needed for the moods and mp3s: brew install ffmpeg (Mac) or sudo apt install ffmpeg"))
    try:
        from .voice import Voice

        eng = home.voice.base.get("engine", "piper")
        have = Voice.available_engine(eng, home.voice.base)
        model = home.voice.base.get("piper_model")
        if have == "piper" and model and not Path(c.resolve_path(model)).exists():
            out.append(_row("Voice", False, f"Piper voice file missing ({Path(model).name})",
                            "Download it: phrasehome download-models (or voice: {engine: say} on a Mac)"))
        else:
            out.append(_row("Voice", have not in ("none", "") or eng == "none", f"{eng} -> {have}",
                            "Install Piper (pip install piper-tts) or set voice: {engine: say} on a Mac"))
    except Exception as e:
        out.append(_row("Voice", False, str(e)[:120], "Check the voice: section of config.yaml"))
    # ---- the AI
    a = c.assistant or {}
    if a:
        provider = str(a.get("provider", "claude")).lower()
        if provider in ("local", "ollama"):
            url = str((a.get("local") or {}).get("url") or a.get("url") or "http://localhost:11434")
            ok, detail = None, url
            if online:
                try:
                    import requests

                    tags = requests.get(url.rstrip("/") + "/api/tags", timeout=3).json()
                    names = [m.get("name", "") for m in tags.get("models", [])]
                    want = str((a.get("local") or {}).get("model") or a.get("model") or "qwen3:8b")
                    ok = any(n.startswith(want) for n in names)
                    detail = f"Ollama up; {want} {'installed' if ok else 'not pulled'}"
                except Exception:
                    ok, detail = False, f"Ollama not answering at {url}"
            out.append(_row("AI (local)", ok, detail, "Start it: ollama serve, and ollama pull qwen3:8b"))
        else:
            key = bool(os.environ.get("ANTHROPIC_API_KEY"))
            out.append(_row("AI (Claude)", key, "API key set" if key else "no API key",
                            "Put ANTHROPIC_API_KEY=... in the .env file, or use provider: local"))
    # ---- phones
    people = [i for i in home.instances.all.values() if not i.main]
    no_phone = [i.name for i in people if not i.phone]
    ntfy = any(b["type"] == "ntfy" or b.get("was") == "ntfy" for b in c.backends.values())
    out.append(_row("Phone alerts", bool(ntfy and not no_phone), (f"no phone topic for {', '.join(no_phone)}" if no_phone else
                                                                  "everyone has a phone topic") + ("" if ntfy else "; no ntfy backend"),
                    "Give each person `phone: their-ntfy-topic` and add backends: {phone: {type: ntfy}}"))
    # ---- devices
    sensors = getattr(home, "sensors", None)
    if sensors is not None:
        off = sorted(getattr(sensors, "offline", set()))
        out.append(_row("ESP32 modules", not off, f"offline: {', '.join(off)}" if off else f"{len(sensors.nodes)} online",
                        "Check its power and Wi-Fi; it reconnects by itself when it's back"))
    cams = getattr(home, "cameras", None)
    if cams is not None and getattr(cams, "feeds", None):
        stale = [n for n, f in cams.feeds.items() if not getattr(f, "jpeg", None)]
        out.append(_row("Cameras", not stale, f"no picture from {', '.join(stale)}" if stale else f"{len(cams.feeds)} sending",
                        "Check the camera's power and address in config.yaml (cameras:)"))
    bad_nodes = node_problems(c)
    if bad_nodes:
        out.append(_row("Device names in config", False, f"{len(bad_nodes)} unknown: {bad_nodes[0]}",
                        "Fix the name in config.yaml, or add the module under backends: esp32: nodes:"))
    # ---- the computer
    data = Path(c.resolve_path(c.data_dir))
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".write-test"
        probe.write_text("ok")
        probe.unlink()
        writable = True
    except OSError:
        writable = False
    out.append(_row("Data folder", writable, str(data), "The house can't save anything: check the folder's permissions"))
    free = shutil.disk_usage(data if data.exists() else Path.cwd()).free / 1e9
    out.append(_row("Disk space", free > 1.0, f"{free:.1f} GB free", "Free some space (old clips in data/, logs)"))
    if c.web.get("host", "0.0.0.0") not in ("127.0.0.1", "localhost") and not c.web.get("token"):
        out.append(_row("Dashboard password", False, "anyone on the Wi-Fi can open it",
                        "Add web: {token: something-long} to config.yaml"))
    # ---- learning and updates
    try:
        from .hearing_train import trained_fixes
        from .understanding import Aliases

        n_alias = len(Aliases(c).all())
        out.append(_row("Learned hearing", True, f"{n_alias} taught wordings, {len(trained_fixes(c))} proven word fixes"))
    except Exception:
        pass
    if online:
        try:
            from .updater import status

            st = status()
            if st.get("ok"):
                out.append(_row("Updates", st["behind"] == 0, "up to date" if not st["behind"] else
                                f"{st['behind']} new commits waiting", "Update now (button below) or: phrasehome update"))
        except Exception as e:
            log.debug("update check: %s", e)
    recent = list(watch_log().items) if _RECENT is not None else []
    errors = [r for r in recent if r["level"] in ("error", "critical")]
    out.append(_row("Recent errors", not errors, f"{len(errors)} in the log lately" if errors else "none",
                    "See the list below; most say what to do"))
    return out


def report(home, online: bool = True) -> dict[str, Any]:
    rows = checks(home, online)
    return {"checks": rows, "problems": sum(1 for r in rows if r["ok"] is False),
            "recent": list(watch_log().items)[-25:][::-1]}


def main(args) -> int:
    from .config import load_config
    from .dispatcher import Dispatcher

    watch_log()
    home = Dispatcher(load_config(args.config), dry_run=True)
    try:
        r = report(home)
    finally:
        home.close()
    for row in r["checks"]:
        mark = {True: "ok  ", False: "FIX ", None: "tip "}[row["ok"]]
        print(f"{mark} {row['name']}: {row['detail']}" + (f"\n       -> {row['fix']}" if row["fix"] else ""))
    print(f"\n{r['problems']} thing{'s' if r['problems'] != 1 else ''} to fix." if r["problems"] else "\nAll good.")
    return 1 if r["problems"] else 0


HEALTH_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Health</title>
<style>
:root{--bg:#f4f4f1;--card:#fff;--ink:#1d1d1b;--mute:#6b6b66;--line:#e2e2dc;--ok:#2f7d4f;--warn:#b3541e;--acc:#2a5db0}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1f1f1d;--ink:#ecebe6;--mute:#9b9a94;--line:#33332f;--ok:#5cb27f;--warn:#e08a52;--acc:#7aa5ee}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{display:flex;gap:10px;align-items:center;justify-content:space-between;padding:16px 20px}h1{font-size:20px;margin:0}
main{display:grid;gap:14px;padding:0 20px 24px;grid-template-columns:minmax(0,2fr) minmax(260px,1fr)}@media (max-width:860px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:0 0 10px}
.row{display:grid;grid-template-columns:24px 1fr;gap:4px 10px;padding:8px 0;border-bottom:1px solid var(--line)}.row:last-child{border:0}
.ok{color:var(--ok)}.bad{color:var(--warn)}.mute{color:var(--mute);font-size:13px}.fix{color:var(--warn);font-size:14px}
button{font:inherit;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink);padding:6px 12px;cursor:pointer}a{color:var(--acc)}
</style>
<header><h1>Health</h1><div><button onclick="load()">Check again</button> <a href="/">Dashboard</a></div></header>
<main><section class="card"><h2>Checks</h2><div id="checks" class="mute">Checking…</div></section>
<div><section class="card"><h2>Update</h2><div id="upd" class="mute"></div><button onclick="upd()">Update now</button>
<div class="mute" style="margin-top:6px">Fast-forward from GitHub, checked first; if the new version fails, the old one stays.</div></section>
<section class="card" style="margin-top:14px"><h2>Recent warnings and errors</h2><div id="recent" class="mute"></div></section></div></main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(p,b){const r=await fetch(p,b?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}:{});if(r.status==401){location.href='/';return null}return r.json()}
async function load(){checks.textContent='Checking…';const j=await api('/api/health');if(!j)return;
 checks.innerHTML=j.checks.map(c=>`<div class="row"><b class="${c.ok===false?'bad':c.ok?'ok':'mute'}">${c.ok===false?'✗':c.ok?'✓':'•'}</b><div><b>${esc(c.name)}</b> <span class=mute>${esc(c.detail)}</span>${c.fix?`<div class="fix">${esc(c.fix)}</div>`:''}</div></div>`).join('');
 recent.innerHTML=j.recent.map(r=>`<div><span class="${r.level=='error'?'bad':''}">${esc(r.time)} ${esc(r.where)}</span>: ${esc(r.message)}</div>`).join('')||'Nothing lately.'}
window.upd=async()=>{document.getElementById('upd').textContent='Updating…';const j=await api('/api/update',{});document.getElementById('upd').textContent=j?(j.updated?`Updated ${j.from} → ${j.to}. Restarting…`:(j.why||'Not updated')):''};
load();
</script>
"""
