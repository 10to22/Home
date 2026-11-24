"""Weather, air quality, pollen and severe-weather alerts. Free, no API key.

- Forecast + air quality/pollen: Open-Meteo (worldwide; pollen in Europe only)
- Official alerts: weather.gov (USA). Elsewhere, big forecast events (heavy rain/snow, storms,
  strong wind, extreme heat/cold) still raise warnings.

weather:
  units: fahrenheit            # or celsius
  refresh_minutes: 30
  umbrella_percent: 50         # rain chance that earns an umbrella reminder
  jacket_below: 50             # degrees (in your units)
  reminder_window: "06:00-13:00"
  door_contact: { node: front door, input: contact }   # remind as the door opens

Events: weather.alert (official warnings), weather.advisory (watches/advisories),
        weather.warning (big forecast events)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from . import clock

log = logging.getLogger("phrasehome.weather")

WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle",
       57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
       67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow",
       80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 85: "snow showers",
       86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
       99: "thunderstorms with hail"}
RAINY = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
STORMY = {95, 96, 99}
POLLEN = ["alder_pollen", "birch_pollen", "grass_pollen", "mugwort_pollen", "olive_pollen",
          "ragweed_pollen"]
UA = {"User-Agent": "phrasehome home automation", "Accept": "application/geo+json"}


def is_major(alert: dict) -> bool:
    """A real warning (tornado, severe thunderstorm, flood, blizzard...), not a watch or advisory."""
    event = alert.get("event", "")
    if alert.get("severity") == "Extreme" and alert.get("certainty") in ("Observed", "Likely", None):
        return True
    return "Warning" in event and alert.get("severity") in ("Severe", "Extreme")


def aqi_words(aqi: float | None) -> str:
    if aqi is None:
        return ""
    for limit, words in ((50, "good"), (100, "moderate"), (150, "unhealthy for sensitive groups"),
                         (200, "unhealthy"), (300, "very unhealthy")):
        if aqi <= limit:
            return words
    return "hazardous"


def pollen_words(v: float) -> str:
    return "very high" if v > 200 else "high" if v > 50 else "moderate" if v > 10 else "low"


class Weather:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("weather") or {})
        loc = home.config.location
        self.lat, self.lon = loc.get("latitude"), loc.get("longitude")
        self.f = str(self.cfg.get("units", "fahrenheit")).lower().startswith("f")
        self.data: dict[str, Any] = {}
        self.air: dict[str, Any] = {}
        self.alerts: list[dict[str, Any]] = []
        self._seen_alerts: set[str] = set()
        self._warned: set[str] = set()
        self._reminded: str = ""
        self._running = False
        home.weather = self
        home.events.subscribe(self._on_event)

    # ---------- fetching ----------
    def fetch(self) -> None:
        if self.lat is None:
            raise RuntimeError("set `location: {latitude, longitude}` for weather")
        p = {"latitude": self.lat, "longitude": self.lon, "timezone": "auto", "forecast_days": 3,
             "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
             "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                      "precipitation_sum,snowfall_sum,wind_speed_10m_max,wind_gusts_10m_max,uv_index_max",
             "hourly": "precipitation_probability,weather_code"}
        if self.f:
            p.update(temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch")
        self.data = requests.get("https://api.open-meteo.com/v1/forecast", params=p, timeout=15).json()
        try:
            self.air = requests.get("https://air-quality-api.open-meteo.com/v1/air-quality", params={
                "latitude": self.lat, "longitude": self.lon, "timezone": "auto",
                "current": "us_aqi,european_aqi,pm2_5," + ",".join(POLLEN)}, timeout=15).json()
        except Exception as e:
            log.debug("air quality: %s", e)
        if self.cfg.get("official_alerts", True):
            try:
                r = requests.get("https://api.weather.gov/alerts/active",
                                 params={"point": f"{self.lat:.4f},{self.lon:.4f}"}, headers=UA, timeout=15)
                if r.ok:
                    self.alerts = [f["properties"] | {"id": f.get("id", "")} for f in r.json().get("features", [])]
            except Exception as e:
                log.debug("weather.gov alerts: %s", e)

    # ---------- reading ----------
    def day(self, offset: int = 0) -> dict[str, Any]:
        d = self.data.get("daily") or {}
        if not d.get("time") or offset >= len(d["time"]):
            return {}
        return {k: (v[offset] if isinstance(v, list) else v) for k, v in d.items()}

    def unit(self) -> str:
        return "degrees"

    def summary(self, offset: int = 0) -> str:
        d = self.day(offset)
        if not d:
            return "I don't have the forecast right now."
        when = ["Today", "Tomorrow", "The day after"][min(offset, 2)]
        parts = [f"{when}: {WMO.get(d.get('weather_code'), 'mixed weather')}",
                 f"high of {round(d['temperature_2m_max'])}, low of {round(d['temperature_2m_min'])}"]
        rain = d.get("precipitation_probability_max")
        if rain is not None and rain >= 20:
            parts.append(f"{round(rain)} percent chance of rain")
        if offset == 0:
            cur = self.data.get("current") or {}
            if cur:
                parts.insert(1, f"it's {round(cur['temperature_2m'])} degrees now")
        text = ", ".join(parts) + "."
        if offset == 0:
            text += self.air_summary()
            alerts = self.active_alerts()
            if alerts:
                text += " Weather alert: " + "; ".join(a["event"] for a in alerts[:2]) + "."
        return text

    def air_summary(self) -> str:
        cur = self.air.get("current") or {}
        out = []
        aqi = cur.get("us_aqi")
        if aqi is not None and aqi > 50:
            out.append(f"Air quality is {aqi_words(aqi)}")
        pollen = {k.replace("_pollen", ""): v for k, v in cur.items() if k in POLLEN and v}
        high = [f"{k} pollen is {pollen_words(v)}" for k, v in sorted(pollen.items(), key=lambda x: -x[1]) if v > 10][:2]
        out += high
        return (" " + ". ".join(s[0].upper() + s[1:] for s in out) + ".") if out else ""

    def active_alerts(self) -> list[dict]:
        return [a for a in self.alerts if is_major(a)]

    def needs(self) -> list[str]:
        """What to take today: umbrella / jacket."""
        d = self.day(0)
        out = []
        if not d:
            return out
        rainy_hours = [c for c in (self.data.get("hourly") or {}).get("weather_code", [])[:18] if c in RAINY]
        if (d.get("precipitation_probability_max") or 0) >= float(self.cfg.get("umbrella_percent", 50)) or len(rainy_hours) >= 2:
            out.append("umbrella")
        cold = float(self.cfg.get("jacket_below", 50 if self.f else 10))
        if d.get("temperature_2m_max") is not None and d["temperature_2m_max"] < cold:
            out.append("jacket")
        return out

    # ---------- warnings ----------
    def check(self) -> None:
        for a in self.alerts:
            if a["id"] in self._seen_alerts:
                continue
            self._seen_alerts.add(a["id"])
            major = is_major(a)
            self.home.events.log("weather.alert" if major else "weather.advisory", event=a.get("event", ""),
                                 headline=a.get("headline", ""), severity=a.get("severity", ""),
                                 instruction=(a.get("instruction") or "")[:400])
        today = clock.now().strftime("%Y-%m-%d")
        for offset in (0, 1):
            d = self.day(offset)
            if not d:
                continue
            for kind, detail in self._forecast_warnings(d):
                key = f"{d['time']}:{kind}"
                if key in self._warned:
                    continue
                self._warned.add(key)
                self.home.events.log("weather.warning", warning=kind, day="today" if offset == 0 else "tomorrow",
                                     detail=detail, date=d["time"], checked=today)

    def _forecast_warnings(self, d: dict) -> list[tuple[str, str]]:
        f = self.f
        t = {"rain": 1.0 if f else 25, "snow": 2.0 if f else 5, "gust": 50 if f else 80,
             "heat": 95 if f else 35, "cold": 10 if f else -12}
        t.update(self.cfg.get("warn", {}))
        out = []
        if d.get("weather_code") in STORMY:
            out.append(("storm", "thunderstorms are expected"))
        if (d.get("precipitation_sum") or 0) >= t["rain"]:
            out.append(("heavy rain", f"heavy rain, about {d['precipitation_sum']:.1f} {'inches' if f else 'millimeters'}"))
        if (d.get("snowfall_sum") or 0) >= t["snow"]:
            out.append(("snow", f"snow, about {d['snowfall_sum']:.0f} {'inches' if f else 'centimeters'}"))
        if (d.get("wind_gusts_10m_max") or 0) >= t["gust"]:
            out.append(("wind", f"wind gusts up to {round(d['wind_gusts_10m_max'])} {'miles' if f else 'kilometers'} per hour"))
        if (d.get("temperature_2m_max") or -99) >= t["heat"]:
            out.append(("heat", f"a high of {round(d['temperature_2m_max'])} degrees"))
        if (d.get("temperature_2m_min") or 99) <= t["cold"]:
            out.append(("cold", f"a low of {round(d['temperature_2m_min'])} degrees"))
        return out

    # ---------- umbrella / jacket at the door ----------
    def _on_event(self, ev: dict) -> None:
        dc = self.cfg.get("door_contact") or {}
        leaving = ev["kind"] in ("presence.left",) or (ev["kind"] == "routine" and ev.get("name") == "leaving") or (
            ev["kind"] == "sensor" and dc and ev.get("node") == dc.get("node") and ev.get("input") == dc.get("input")
            and ev.get("state") == "off")
        if leaving:
            threading.Thread(target=self.remind_at_door, daemon=True).start()

    def remind_at_door(self) -> str | None:
        now = clock.now()
        today = now.strftime("%Y-%m-%d")
        if self._reminded == today or not clock.in_window(self.cfg.get("reminder_window", "06:00-13:00"), now):
            return None
        need = self.needs()
        if not need:
            return None
        self._reminded = today
        d = self.day(0)
        reason = []
        if "umbrella" in need:
            reason.append(f"{round(d.get('precipitation_probability_max') or 0)} percent chance of rain")
        if "jacket" in need:
            reason.append(f"a high of only {round(d['temperature_2m_max'])}")
        text = f"Take {' and a '.join(['an umbrella' if n == 'umbrella' else 'a jacket' for n in need])}: {' and '.join(reason)} today."
        room = self.cfg.get("reminder_room") or next(iter(self.home.config.doors.values()), {}).get("room")
        self.home.voice.speak(text, room=room)
        return text

    # ---------- loop ----------
    def start(self) -> None:
        if self.lat is None:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self.fetch()
                self.check()
            except Exception as e:
                log.warning("weather: %s", e)
            time.sleep(float(self.cfg.get("refresh_minutes", 30)) * 60)
