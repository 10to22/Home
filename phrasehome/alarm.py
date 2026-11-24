"""A gentle home alarm (deliberately not strict).

- It arms itself when the house goes into away (or vacation) mode, after an exit delay.
- When a watched door/window opens or motion is seen while armed, it doesn't panic: it says
  "the alarm is on" and gives a grace period to disarm by face scan, fingerprint, saying
  "disarm" / "I'm home", the dashboard, or a family member's phone coming home.
- Only if nobody disarms does it alert: a spoken warning and a phone notification (with a camera
  photo). No siren unless you add one. It re-arms quietly after a few minutes.

alarm:
  arm_on_modes: [away, vacation]
  exit_delay: 90                   # seconds to leave after arming
  entry_delay: 60                  # seconds to disarm after coming in
  watch:                           # what counts as "someone's inside"
    - { node: front door, input: contact, state: off }
    - { node: hallway, input: motion, state: on }
  perimeter_people: false          # also start the grace period for a person on outdoor cameras
  on_alert: [ ...steps... ]        # default: warning in every room + phone alert

Events: alarm.arming, alarm.armed, alarm.entry, alarm.alert, alarm.disarmed
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("phrasehome.alarm")

DEFAULT_ALERT = [
    {"backend": "voice", "speak": "alarm_alert", "everywhere": True, "urgent": True},
]


class AlarmSystem:
    def __init__(self, home) -> None:
        self.home = home
        cfg = dict(home.config.extra.get("alarm") or {})
        self.cfg = cfg
        self.arm_modes = [m.lower() for m in cfg.get("arm_on_modes", ["away", "vacation"])]
        self.exit_delay = float(cfg.get("exit_delay", 90))
        self.entry_delay = float(cfg.get("entry_delay", 60))
        self.rearm_after = float(cfg.get("rearm_minutes", 5)) * 60
        self.watch = cfg.get("watch") or []
        self.on_alert = cfg.get("on_alert") or DEFAULT_ALERT
        ntfy = next((n for n, b in home.config.backends.items() if b["type"] == "ntfy"), None)
        if ntfy and not cfg.get("on_alert"):
            self.on_alert = DEFAULT_ALERT + [{"backend": ntfy, "title": "Home alarm", "priority": 5,
                                              "message": "Nobody disarmed the alarm after {trigger}",
                                              "attach": "{photo}"}]
        self.state = "disarmed"
        self._token = 0  # invalidates pending timers when the state changes
        self._trigger: dict[str, Any] = {}
        home.alarm = self
        home.events.subscribe(self._on_event)

    def _set(self, state: str, **data) -> None:
        self.state = state
        self._token += 1
        self.home.events.log(f"alarm.{state}", **data)

    # ---------- arming ----------
    def arm(self, delay: float | None = None) -> str:
        if self.state in ("armed", "arming"):
            return "already on"
        self._set("arming")
        tok = self._token
        d = self.exit_delay if delay is None else delay
        self.home.timeline.call_in(d, lambda: self._armed(tok))
        return f"arming in {int(d)} seconds"

    def _armed(self, tok: int) -> None:
        if tok == self._token and self.state == "arming":
            self._set("armed")

    def disarm(self, by: str = "") -> str:
        if self.state == "disarmed":
            return "already off"
        was = self.state
        self._set("disarmed", by=by)
        if was in ("entry", "alert"):
            self.home.voice.speak("alarm_disarmed", room=self._trigger.get("room"))
        return "disarmed"

    # ---------- reacting ----------
    def _matches_watch(self, ev: dict) -> bool:
        if ev["kind"] == "sensor":
            for w in self.watch:
                if all(_word(ev.get(k, "")) == _word(v) for k, v in w.items()):
                    return True
        if ev["kind"] == "camera" and ev.get("detected") == "person" and self.cfg.get("perimeter_people"):
            return True
        return False

    def _on_event(self, ev: dict) -> None:
        k = ev["kind"]
        if k == "mode":
            if ev.get("mode") in self.arm_modes:
                threading.Thread(target=self.arm, daemon=True).start()
            elif self.state != "disarmed":
                self.disarm(by=f"mode {ev.get('mode')}")
            return
        if self.state == "disarmed" or k.startswith("alarm."):
            return
        # anything that proves it's family disarms (that's the "not strict" part)
        if (k == "scan" and ev.get("result") == "allowed") or k in ("fingerprint.accepted", "presence.arrived"):
            self.disarm(by=ev.get("name") or ev.get("person") or k)
            if self.home.state.mode in self.arm_modes:
                self.home.state.set_mode("home", reason="alarm disarmed")
            return
        if self.state == "armed" and self._matches_watch(ev):
            self._trigger = {"trigger": f"{ev.get('input') or ev.get('detected')} at {ev.get('node') or ev.get('camera')}",
                             "room": ev.get("room") or ev.get("node"), "photo": ev.get("photo", "")}
            self._set("entry", **self._trigger)
            self.home.voice.speak("alarm_entry", rooms=[self._trigger["room"]] if self._trigger["room"] else None,
                                  everywhere=not self._trigger["room"], urgent=True,
                                  seconds=int(self.entry_delay))
            tok = self._token
            self.home.timeline.call_in(self.entry_delay, lambda: self._alert(tok))

    def _alert(self, tok: int) -> None:
        if tok != self._token or self.state != "entry":
            return
        photo = self._trigger.get("photo") or self._camera_photo()
        self._set("alert", **{**self._trigger, "photo": photo})
        values = {**self._trigger, "photo": photo}
        threading.Thread(target=self.home.run_steps, args=(self.on_alert, values, "alarm"), daemon=True).start()
        tok2 = self._token
        self.home.timeline.call_in(self.rearm_after, lambda: self._rearm(tok2))

    def _rearm(self, tok: int) -> None:
        if tok == self._token and self.state == "alert":
            self._set("armed")

    def _camera_photo(self) -> str:
        cams = getattr(self.home, "cameras", None)
        if not cams:
            return ""
        for f in list(cams.find("indoor")) + list(cams.feeds.values()):
            frame, _, _ = f.latest()
            if frame is not None:
                return cams._save(f.name, frame)
        return ""

    def start(self) -> None:
        if self.home.state.mode in self.arm_modes:
            self.state = "armed"  # survive a restart while away

    def stop(self) -> None:
        pass


def _word(v) -> str:
    """YAML turns a bare on/off into True/False; compare them as words."""
    return "on" if v is True else "off" if v is False else str(v).lower()
