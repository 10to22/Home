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
    if home.config.web.get("enabled", True):
        try:
            from .web import WebServer

            add(WebServer(home))
        except Exception as e:
            log.error("dashboard not started: %s", e)
    return services
