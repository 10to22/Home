"""Starts the hub's background services that the config asks for."""

from __future__ import annotations

import logging

log = logging.getLogger("phrasehome")


def start_services(home) -> list:
    services = []

    def add(svc):
        svc.start()
        services.append(svc)

    try:
        home.scheduler.start()  # reminders, alarms, timers, schedules, vacation lights
        services.append(home.scheduler)
    except Exception as e:
        log.error("scheduler not started: %s", e)
    try:
        from .sensors import SensorWatcher

        add(SensorWatcher(home))
    except Exception as e:
        log.error("sensor watcher not started: %s", e)
    try:
        from .presence import Presence

        add(Presence(home))
    except Exception as e:
        log.error("presence not started: %s", e)
    for fp in home.config.fingerprint:
        try:
            from .fingerprint import FingerprintDoor

            add(FingerprintDoor(home, fp))
        except Exception as e:
            log.error("fingerprint sensor for %s not started: %s", fp.get("door"), e)
    optional = [
        ("cameras", lambda: home.config.cameras, "vision", "CameraHub"),
        ("weather", lambda: "latitude" in home.config.location, "weather", "Weather"),
        ("alarm", lambda: "alarm" in home.config.extra, "alarm", "AlarmSystem"),
        ("energy", lambda: bool((home.config.extra.get("energy") or {}).get("plugs")), "energy", "Energy"),
        ("packages", lambda: "packages" in home.config.extra, "packages", "Packages"),
        ("meals", lambda: True, "meals", "Meals"),
        ("spotify", lambda: "spotify" in home.config.extra, "spotify", "Spotify"),
        ("rooms", lambda: True, "rooms", "RoomTracker"),
        ("commute", lambda: any(i.extra.get("commute") for i in home.instances.all.values()),
         "commute", "Commute"),
        ("pets", lambda: "pets" in home.config.extra, "pets", "Pets"),
        ("diary", lambda: bool(home.config.assistant), "diary", "Diary"),
    ]
    for label, wanted, module, cls in optional:
        if not wanted():
            continue
        try:
            mod = __import__(f"phrasehome.{module}", fromlist=[cls])
            add(getattr(mod, cls)(home))
        except Exception as e:
            log.error("%s not started: %s", label, e)
    if home.config.extra.get("sim"):  # `simulate`: where the laptop's mic and webcam are
        try:
            from .simulator import Placement

            home.sim = Placement(home)
            add(home.sim)
        except Exception as e:
            log.error("laptop placement not started: %s", e)
    if home.config.web.get("enabled", True):
        try:
            from .web import WebServer

            add(WebServer(home))
        except Exception as e:
            log.error("dashboard not started: %s", e)
    return services
