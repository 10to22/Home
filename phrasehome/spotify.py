"""Spotify: play by voice on the house speakers, "what song is this?", "like this song".

Two parts:
1. Playback: librespot makes the house a Spotify Connect speaker called "House" and feeds the
   ESP32 speaker group (set the speakers backend's `source:` to librespot; see README).
2. Control: the Spotify Web API (your own free developer app). Needs Spotify Premium.

spotify:
  client_id_env: SPOTIFY_CLIENT_ID
  client_secret_env: SPOTIFY_CLIENT_SECRET
  device: House                 # librespot's --name
  speakers: music               # speakers backend to switch to group mode when playing
  playlists:                    # optional nicknames used by voice and routines
    dinner: spotify:playlist:37i9dQZF1DX4xuWVBs4FgJ
    focus: spotify:playlist:37i9dQZF1DWZeKCadgRdKQ

One-time login:  phrasehome auth-spotify   (saves data/spotify.json)

Actions: {spotify: play, query: "jazz", rooms: [kitchen]} {spotify: play, playlist: dinner}
         {spotify: pause | resume | next | previous | like | what | volume, level: 40}
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("phrasehome.spotify")

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"
AUTH_URL = "https://accounts.spotify.com/authorize"
SCOPES = ("user-read-playback-state user-modify-playback-state user-read-currently-playing "
          "user-library-modify user-library-read playlist-read-private")
REDIRECT = "http://127.0.0.1:8888/callback"


class Spotify:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("spotify") or {})
        self.client_id = self.cfg.get("client_id") or os.environ.get(self.cfg.get("client_id_env", "SPOTIFY_CLIENT_ID"), "")
        self.client_secret = self.cfg.get("client_secret") or os.environ.get(
            self.cfg.get("client_secret_env", "SPOTIFY_CLIENT_SECRET"), "")
        self.device_name = str(self.cfg.get("device", "House"))
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "spotify.json"
        self._token = ""
        self._expires = 0.0
        home.spotify = self

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    # ---------- auth ----------
    def _basic(self) -> dict:
        raw = f"{self.client_id}:{self.client_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def login_url(self) -> str:
        from urllib.parse import urlencode

        return AUTH_URL + "?" + urlencode({"client_id": self.client_id, "response_type": "code",
                                           "redirect_uri": REDIRECT, "scope": SCOPES})

    def exchange(self, code: str) -> None:
        r = requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                           "redirect_uri": REDIRECT}, headers=self._basic(), timeout=15)
        r.raise_for_status()
        j = r.json()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"refresh_token": j["refresh_token"]}))
        self.path.chmod(0o600)
        self._token, self._expires = j["access_token"], time.time() + j.get("expires_in", 3600) - 60

    def token(self) -> str:
        if self._token and time.time() < self._expires:
            return self._token
        if not self.path.exists():
            raise RuntimeError("Spotify isn't linked yet: run `phrasehome auth-spotify`")
        refresh = json.loads(self.path.read_text())["refresh_token"]
        r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": refresh},
                          headers=self._basic(), timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("refresh_token"):
            self.path.write_text(json.dumps({"refresh_token": j["refresh_token"]}))
        self._token, self._expires = j["access_token"], time.time() + j.get("expires_in", 3600) - 60
        return self._token

    def api(self, method: str, path: str, **kw) -> Any:
        r = requests.request(method, API + path, headers={"Authorization": f"Bearer {self.token()}"},
                             timeout=15, **kw)
        if r.status_code == 404 and "player" in path:
            raise RuntimeError("no active Spotify speaker: is librespot running?")
        r.raise_for_status()
        return r.json() if r.content else None

    # ---------- control ----------
    def device_id(self) -> str | None:
        devs = (self.api("GET", "/me/player/devices") or {}).get("devices", [])
        hit = next((d for d in devs if d["name"].lower() == self.device_name.lower()), None)
        return hit["id"] if hit else None

    def find(self, query: str, kind: str = "any") -> tuple[str, str]:
        """Returns (uri, description). kind: track, artist, album, playlist, or any."""
        nick = (self.cfg.get("playlists") or {}).get(query.lower())
        if nick:
            return nick, query
        mine = (self.api("GET", "/me/playlists", params={"limit": 50}) or {}).get("items", [])
        hit = next((p for p in mine if p and p["name"].lower() == query.lower()), None)
        if hit and kind in ("any", "playlist"):
            return hit["uri"], f"your playlist {hit['name']}"
        types = "track,artist,album,playlist" if kind == "any" else kind
        res = self.api("GET", "/search", params={"q": query, "type": types, "limit": 1}) or {}
        order = ["artists", "playlists", "albums", "tracks"] if kind == "any" else [kind + "s"]
        for key in order:
            items = [i for i in (res.get(key) or {}).get("items", []) if i]
            if items:
                i = items[0]
                who = ", ".join(a["name"] for a in i.get("artists", [])[:2])
                return i["uri"], (f"{i['name']} by {who}" if who else i["name"])
        raise ValueError(f"couldn't find '{query}' on Spotify")

    def play(self, query: str = "", kind: str = "any", rooms: list[str] | None = None,
             playlist: str = "") -> str:
        if self.cfg.get("simulated"):  # `phrasehome simulate`: pretend, don't touch the account
            what = playlist or query
            log.info("[spotify, simulated] play %s in %s", what or "(resume)", ", ".join(rooms or ["all"]))
            return f"Playing {what}." if what else "Resuming."
        self._speakers(rooms)
        dev = self.device_id()
        params = {"device_id": dev} if dev else {}
        if not (query or playlist):
            self.api("PUT", "/me/player/play", params=params)
            return "Resuming."
        uri, desc = self.find(playlist or query, "playlist" if playlist else kind)
        body = {"uris": [uri]} if uri.startswith("spotify:track:") else {"context_uri": uri}
        self.api("PUT", "/me/player/play", params=params, json=body)
        return f"Playing {desc}."

    def _speakers(self, rooms: list[str] | None) -> None:
        name = self.cfg.get("speakers")
        if not name:
            return
        try:
            hub = getattr(self.home.backend(name), "hub", None)
            if hub is None:
                return
            for r in rooms or ["all"]:
                hub.set_mode(r, "group")
        except Exception as e:
            log.warning("couldn't put speakers in group mode: %s", e)

    def simple(self, what: str, level: Any = None) -> str:
        if self.cfg.get("simulated") and what != "resume":
            log.info("[spotify, simulated] %s %s", what, level if level is not None else "")
            return {"what": "Nothing's playing in the simulation.", "like": "Saved."}.get(what, "Okay.")
        if what == "pause":
            self.api("PUT", "/me/player/pause")
            return "Paused."
        if what == "resume":
            return self.play()
        if what == "next":
            self.api("POST", "/me/player/next")
            return "Skipping."
        if what == "previous":
            self.api("POST", "/me/player/previous")
            return "Going back."
        if what == "volume":
            self.api("PUT", "/me/player/volume", params={"volume_percent": max(0, min(100, int(level or 50)))})
            return f"Volume {level}."
        if what == "shuffle":
            self.api("PUT", "/me/player/shuffle", params={"state": "true"})
            return "Shuffling."
        if what in ("what", "like"):
            cur = self.api("GET", "/me/player/currently-playing") or {}
            item = cur.get("item")
            if not item:
                return "Nothing's playing right now."
            desc = f"{item['name']} by {', '.join(a['name'] for a in item.get('artists', [])[:2])}"
            if what == "what":
                return f"This is {desc}."
            self.api("PUT", "/me/tracks", params={"ids": item["id"]})
            return f"Saved {desc} to your liked songs."
        raise ValueError(f"unknown spotify action '{what}'")


def login(home, open_browser: bool = True) -> None:
    """One-time: opens Spotify's login page and catches the redirect on 127.0.0.1:8888."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    sp = Spotify(home)
    if not (sp.client_id and sp.client_secret):
        raise SystemExit("set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET (see README: Spotify)")
    got: dict[str, str] = {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            got["code"] = q.get("code", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Spotify linked. You can close this tab.")

    url = sp.login_url()
    print(f"Open this link and log in to Spotify:\n{url}")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    srv = HTTPServer(("127.0.0.1", 8888), H)
    while "code" not in got:
        srv.handle_request()
    sp.exchange(got["code"])
    print(f"Linked. Saved {sp.path}")
