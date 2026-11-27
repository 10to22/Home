"""House Radio: music, with the house as the host between songs.

"house radio" / "radio mode": the playlist starts (Spotify), and every few songs the host cuts in
for a few seconds of house news: the time and weather, who's home, what's coming up (the house
meeting, someone's exam tomorrow, quiet time at the party), the week's records, birthdays, bin
night. With the AI it's written fresh each time in the house's own style; without, from templates.
The host may say what the last song was, but never quotes or paraphrases song lyrics.
"radio off" / "stop the radio".

radio:
  playlist: "house radio"
  every_songs: 3       # with Spotify; without, every_minutes
  every_minutes: 12
  rooms: [living room, kitchen]
  ai: true
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.radio")

HOST_SYSTEM = (
    "You are the host of House Radio, a tiny radio show played in a shared student house between songs. "
    "Speak for 2 to 3 short sentences, like a real radio host: warm, quick, a bit of banter, in the house's "
    "own voice style. Use only the house news you're given. You may name the song and artist that just played, "
    "but never quote, paraphrase, sing or summarize any song lyrics. No lists, no emoji, no stage directions. "
    "Start with a [mood] tag such as [cheerful] or [cheeky].")


class Radio:
    def __init__(self, home) -> None:
        self.home = home
        c = home.config.extra.get("radio") or {}
        self.cfg = c
        self.every_songs = int(c.get("every_songs", 3))
        self.every_minutes = float(c.get("every_minutes", 12))
        self.on = False
        self._track: str | None = None
        self._songs = 0
        self._next = 0.0
        self._last_poll = 0.0
        self._last_song = ""
        self._busy = threading.Lock()
        self.segments = 0

    def rooms(self) -> list[str] | None:
        r = self.cfg.get("rooms") or self.home.instances.main.rooms
        return [str(x).lower() for x in r] or None

    def _spotify(self):
        sp = getattr(self.home, "spotify", None)
        return sp if sp is not None else None

    def start(self, room: str | None = None) -> str:
        if self.on:
            return "the radio's already on"
        self.on = True
        self._songs, self._track, self.segments = 0, None, 0
        self._next = time.time() + self.every_minutes * 60
        sp = self._spotify()
        if sp is not None:
            try:
                sp.play(playlist=str(self.cfg["playlist"])) if self.cfg.get("playlist") else sp.play()
            except Exception as e:
                log.warning("radio music: %s", e)
        self.home.events.log("radio.on")
        self.home.voice.speak("radio_on", rooms=[room] if room else self.rooms())
        return "house radio on"

    def stop(self) -> str:
        if not self.on:
            return "the radio isn't on"
        self.on = False
        self.home.events.log("radio.off", segments=self.segments)
        self.home.voice.speak("radio_off", rooms=self.rooms())
        return "house radio off"

    # ---------- when to talk ----------
    def tick(self) -> None:
        if not self.on or self._busy.locked():
            return
        life = getattr(self.home, "life", None)
        if life and life.exams.quiet_now():
            return  # exam quiet: music only
        sp = self._spotify()
        real = sp is not None and not (getattr(sp, "cfg", {}) or {}).get("simulated")
        if real:
            if time.time() - self._last_poll < 8:
                return
            self._last_poll = time.time()
            try:
                cur = sp.api("GET", "/me/player/currently-playing") or {}
            except Exception as e:
                log.debug("radio: %s", e)
                return
            item = cur.get("item") or {}
            tid = item.get("id")
            if tid and tid != self._track:
                if self._track is not None:
                    self._songs += 1
                self._track = tid
                prev, self._last_song = self._last_song, _song(item)
                if self._songs >= self.every_songs:
                    self._songs = 0
                    threading.Thread(target=self.segment, args=(prev, True), daemon=True).start()
            return
        if time.time() >= self._next:
            self._next = time.time() + self.every_minutes * 60
            threading.Thread(target=self.segment, daemon=True).start()

    # ---------- what to say ----------
    def news(self) -> dict[str, Any]:
        h = self.home
        now = clock.now()
        out: dict[str, Any] = {"time": clock.spoken_time(now), "day": now.strftime("%A")}
        w = getattr(h, "weather", None)
        if w is not None:
            try:
                d = w.day(0) or {}
                cur = (w.data or {}).get("current") or {}
                if cur.get("temperature_2m") is not None:
                    out["weather"] = f"{round(cur['temperature_2m'])} degrees"
                if d.get("precipitation_probability_max") is not None and d["precipitation_probability_max"] >= 40:
                    out["rain"] = f"{round(d['precipitation_probability_max'])} percent chance of rain"
            except Exception:
                pass
        names = {i.id: i.name for i in h.instances.all.values()}
        home_now = [names.get(p, p.title()) for p in h.state.home_people()]
        if home_now:
            out["home"] = home_now
        life = getattr(h, "life", None)
        if life:
            b = life.meeting.booked()
            if b:
                from .meeting import _when

                out["house_meeting"] = _when(__import__("datetime").datetime.fromisoformat(b["at"]))
            tom = life.exams.tomorrow()
            if tom:
                out["exams_tomorrow"] = [f"{i.name} ({e['subject']})" for i, e in tom]
            p = life.party.status()
            if p:
                out["party_quiet_time"] = p["quiet_at"]
            rec = life.watch.shower_record(7)
            if rec and rec[1] >= 15:
                out["shower_record"] = f"{names.get(rec[0], 'someone')}, {rec[1]} minutes"
            walking = life.walk.status()
            if walking:
                out["on_the_way_home"] = [x["name"] for x in walking]
        try:
            from .ai_plus import Facts, birthdays

            soon = birthdays(h, 7)
            if soon:
                out["birthdays"] = [f"{n} in {d} days" if d else f"{n} today" for n, d in soon]
            facts = Facts(h).visible(h.instances.main)
            for k, v in facts.items():
                if "bin" in k.lower():
                    out["bins"] = str(v)
        except Exception as e:
            log.debug("radio facts: %s", e)
        return out

    def script(self, last_song: str = "") -> str:
        news = self.news()
        if self.cfg.get("ai", True) and getattr(self.home, "assistant", None) is not None:
            try:
                from . import ai

                prompt = (f"House news right now: {news}." + (f" The song that just finished: {last_song}." if last_song else "")
                          + " Write the next radio break.")
                text = ai.ask(self.home, prompt, system=HOST_SYSTEM, quick=True, max_tokens=400)
                if isinstance(text, str) and text.strip():
                    return no_lyrics(text.strip())
            except Exception as e:
                log.info("radio host falling back to templates: %s", e)
        bits = []
        if last_song:
            bits.append(f"That was {last_song}.")
        bits.append(f"It's {news['time']} on House Radio.")
        if news.get("weather"):
            bits.append(f"Outside it's {news['weather']}" + (f", with a {news['rain']}." if news.get("rain") else "."))
        if news.get("exams_tomorrow"):
            bits.append(f"Good luck tomorrow to {_join(news['exams_tomorrow'])}. Keep it down tonight, everyone.")
        if news.get("house_meeting"):
            bits.append(f"Reminder: house meeting {news['house_meeting']}.")
        if news.get("party_quiet_time"):
            bits.append(f"Quiet time tonight is {news['party_quiet_time']}.")
        if news.get("shower_record"):
            bits.append(f"This week's longest shower: {news['shower_record']}. Legendary. Not in a good way.")
        if news.get("birthdays"):
            bits.append(f"Birthdays coming up: {_join(news['birthdays'])}.")
        if news.get("on_the_way_home"):
            bits.append(f"On the way home: {_join(news['on_the_way_home'])}.")
        if len(bits) <= 2 and news.get("home"):
            bits.append(f"In the house right now: {_join(news['home'])}.")
        bits.append("Back to the music.")
        return "[cheerful] " + " ".join(bits)

    def segment(self, last_song: str = "", pause: bool = False) -> str:
        if not self._busy.acquire(blocking=False):
            return ""
        try:
            sp = self._spotify()
            paused = False
            if pause and sp is not None:
                try:
                    sp.simple("pause")
                    paused = True
                except Exception as e:
                    log.debug("radio pause: %s", e)
            text = self.script(last_song)
            self.segments += 1
            self.home.events.log("radio.segment")
            self.home.voice.speak(text, rooms=self.rooms())
            if paused:
                try:
                    sp.simple("resume")
                except Exception as e:
                    log.debug("radio resume: %s", e)
            return text
        finally:
            self._busy.release()


def _song(item: dict) -> str:
    who = ", ".join(a.get("name", "") for a in (item.get("artists") or [])[:2])
    return f"{item.get('name', '')} by {who}" if who else str(item.get("name", ""))


def no_lyrics(text: str) -> str:
    """The host names songs; it never reads lyrics. Drops anything quoted that runs past a few words."""
    text = re.sub(r"[\"“”][^\"“”]{30,}[\"“”]", "", text)
    text = re.sub(r"♪[^♪]*♪?", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _join(items: list[str]) -> str:
    items = [str(i) for i in items if str(i).strip()]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]
