"""A hearing server: Whisper on a laptop (a Mac's M-series chip does it fast), used by the house over Wi-Fi.

On the laptop:
    phrasehome hearing-server                       # port 8765, distil-medium.en on a 16 GB Mac
    phrasehome hearing-server --model small.en --token some-secret

On the house (config.yaml), either:
    settings: { recognizer: whisper, whisper_server: http://finns-mac.local:8765 }   # all hearing on the laptop
    settings: { second_opinion: http://finns-mac.local:8765 }                         # Vosk hears, the laptop
                                                                                      # double-checks near misses
If the laptop's asleep or away, the house carries on with what it has (Vosk, or Whisper of its own).
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("phrasehome.hearing")


class RemoteWhisper:
    """Talks to a hearing server; looks like hearing.Whisper to the listener."""

    def __init__(self, url: str, token: str = "", timeout: float = 12.0) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.name = f"remote {self.url}"
        self.beam = 0
        self.hotwords = ""
        self._down_until = 0.0

    def available(self) -> bool:
        import requests

        try:
            r = requests.get(self.url + "/health", timeout=3, headers=self._headers())
            if r.ok:
                self.name = f"{r.json().get('model', 'whisper')} on {self.url}"
            return r.ok
        except Exception:
            return False

    def _headers(self) -> dict:
        return {"X-Token": self.token} if self.token else {}

    def transcribe(self, pcm: bytes, prompt: str = "", language: str = "en") -> tuple[str, float, list[dict]]:
        import requests

        if time.monotonic() < self._down_until:
            raise RuntimeError("hearing server unreachable (retrying in a bit)")
        try:
            r = requests.post(self.url + "/transcribe", timeout=self.timeout, headers=self._headers(),
                              json={"pcm": base64.b64encode(pcm).decode(), "prompt": prompt, "language": language,
                                    "hotwords": self.hotwords})
            r.raise_for_status()
        except Exception as e:
            self._down_until = time.monotonic() + 30  # don't hold every sentence up while it's away
            raise RuntimeError(f"hearing server: {e}") from None
        d = r.json()
        return str(d.get("text", "")), float(d.get("conf", 0.0)), list(d.get("words") or [])


def serve(port: int = 8765, model: str = "", token: str = "", host: str = "0.0.0.0", whisper=None):
    """Run the server (blocking). `whisper`: for tests, anything with .transcribe and .name."""
    if whisper is None:
        from .hearing import Whisper
        from .speech_tuning import whisper_beam

        class _Cfg:  # whisper_beam only needs settings.whisper_beam
            class settings:
                whisper_beam = 0

        whisper = Whisper(model, beam=whisper_beam(_Cfg))
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _ok(self) -> bool:
            return not token or self.headers.get("X-Token") == token

        def do_GET(self):
            if not self._ok():
                return self._send(401, {"error": "token"})
            if self.path.startswith("/health"):
                return self._send(200, {"ok": True, "model": getattr(whisper, "name", "whisper")})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._ok():
                return self._send(401, {"error": "token"})
            if not self.path.startswith("/transcribe"):
                return self._send(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                pcm = base64.b64decode(body.get("pcm", ""))
                if hasattr(whisper, "hotwords"):
                    whisper.hotwords = str(body.get("hotwords") or "")
                t0 = time.monotonic()
                with lock:  # one sentence at a time: the model's busy enough
                    text, conf, words = whisper.transcribe(pcm, str(body.get("prompt") or ""),
                                                           str(body.get("language") or "en"))
                self._send(200, {"text": text, "conf": conf, "words": words,
                                 "seconds": round(time.monotonic() - t0, 3)})
            except Exception as e:
                log.warning("hearing server: %s", e)
                self._send(500, {"error": str(e)})

    srv = ThreadingHTTPServer((host, port), Handler)
    return srv


def main(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    srv = serve(args.port, args.model, args.token or "")
    import socket

    print(f"Hearing server on http://{socket.gethostname()}:{srv.server_address[1]}  "
          f"(in the house's config: settings: {{ second_opinion: http://{socket.gethostname()}:{srv.server_address[1]} }})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
