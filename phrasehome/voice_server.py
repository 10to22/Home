"""`phrasehome voice serve`: lend a laptop's voice to the house.

Run it on a Mac or PC with the big voice (Chatterbox or MeloTTS) set up in its config.yaml, then on
the Pi:

    voice:
      engine: http
      http: { url: "http://<laptop>:8810/v1/audio/speech", token_env: PHRASEHOME_VOICE_TOKEN }

It speaks the OpenAI-style speech API (POST /v1/audio/speech {"input": "...", "emotion": "cheeky"}),
answers with a wav, and does the mood itself (its own tuning). GET /health says which engine it runs.
Set a token (--token, or PHRASEHOME_VOICE_TOKEN) so only your house can use it.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("phrasehome.voice_server")

MAX_TEXT = 1000


class VoiceServer:
    def __init__(self, voice, host: str = "0.0.0.0", port: int = 8810, token: str | None = None) -> None:
        self.voice = voice
        self.host, self.port = host, port
        self.token = token if token is not None else os.environ.get("PHRASEHOME_VOICE_TOKEN", "")
        self.httpd: ThreadingHTTPServer | None = None
        # the server runs the big engine itself: an "http" engine here would call itself
        if self.voice.base.get("engine") == "http":
            raise ValueError("this computer's voice is engine: http; set it to melo, chatterbox, say or piper "
                             "here, it's the one that does the talking")

    def speech(self, body: dict) -> bytes:
        from .voice_style import mood_name, split_tag

        tagged, text = split_tag(str(body.get("input") or body.get("text") or ""))
        if not text.strip():
            raise ValueError("no text to say")
        if len(text) > MAX_TEXT:
            raise ValueError(f"text too long (over {MAX_TEXT} characters)")
        v = dict(self.voice.base)
        mood = mood_name(body.get("emotion")) or tagged
        if mood and not self.voice.style.enabled:
            mood = None
        # the asking house's own speed (base_speed: without the mood, which is applied here)
        speed = body.get("base_speed") if mood else None
        speed = speed or body.get("speed")
        if speed:
            v["speed"] = min(3.0, max(0.3, float(speed)))
        wav = self.voice.synthesize(text, v, emotion=mood) if mood else self.voice.synthesize(text, v)
        if wav is None:
            raise ValueError("this computer's voice is off (engine: none)")
        return wav.read_bytes()

    def start(self) -> None:
        server = self

        class Handler(_Handler):
            srv = server

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self.httpd.server_port
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        log.info("voice server on http://%s:%d/v1/audio/speech%s", self.host, self.port,
                 "" if self.token else "  (no token: anyone on your network can use it)")

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()


class _Handler(BaseHTTPRequestHandler):
    srv: VoiceServer

    def log_message(self, *a) -> None:
        pass

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ok(self) -> bool:
        if not self.srv.token:
            return True
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return hmac.compare_digest(got, self.srv.token)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            v = self.srv.voice
            return self._send(200, {"ok": True, "engine": v.available_engine(v.base.get("engine", "piper"), v.base),
                                    "accent": v.style.accent, "emotion": v.style.enabled})
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._ok():
            return self._send(401, {"error": "token required"})
        if self.path.rstrip("/") not in ("/v1/audio/speech", "/speech"):
            return self._send(404, {"error": "not found"})
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            body = json.loads(self.rfile.read(n) or b"{}")
            self._send(200, self.srv.speech(body), "audio/wav")
        except Exception as e:
            log.warning("voice server: %s", e)
            self._send(400, {"error": str(e)})
