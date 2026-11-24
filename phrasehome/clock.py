"""Time helpers: local time, "HH:MM" and day specs, time windows, sunrise/sunset."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def now() -> datetime:
    return datetime.now().astimezone()


def parse_hhmm(s: str) -> time:
    s = str(s).strip().lower().replace(".", ":")
    pm = s.endswith("pm")
    am = s.endswith("am")
    s = s.rstrip("apm ").strip()
    h, _, m = s.partition(":")
    hour, minute = int(h), int(m or 0)
    if pm and hour < 12:
        hour += 12
    if am and hour == 12:
        hour = 0
    return time(hour, minute)


def day_matches(spec, d: date) -> bool:
    """spec: daily | weekdays | weekends | "mon,wed,fri" | ["mon", "fri"]"""
    if spec in (None, "", "daily", "every day", "everyday"):
        return True
    wd = d.weekday()
    if spec == "weekdays":
        return wd < 5
    if spec == "weekends":
        return wd >= 5
    days = spec if isinstance(spec, list) else str(spec).split(",")
    return DAY_NAMES[wd] in {str(x).strip().lower()[:3] for x in days}


def in_window(window: str, t: datetime) -> bool:
    """window like "22:00-07:00" (may wrap past midnight)."""
    a, _, b = str(window).partition("-")
    start, end = parse_hhmm(a), parse_hhmm(b)
    cur = t.time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def sun_times(d: date, lat: float, lon: float, tz=None) -> tuple[datetime | None, datetime | None]:
    """Sunrise and sunset (NOAA approximation, ~1-2 min accuracy). None near the poles."""
    n = d.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    x = math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    if x < -1 or x > 1:
        return None, None
    ha = math.degrees(math.acos(x))
    midnight = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    tz = tz or now().tzinfo
    rise = midnight + timedelta(minutes=720 - 4 * (lon + ha) - eqtime)
    sett = midnight + timedelta(minutes=720 - 4 * (lon - ha) - eqtime)
    return rise.astimezone(tz), sett.astimezone(tz)


def spoken_time(t: datetime) -> str:
    return t.strftime("%I:%M %p").lstrip("0")


def spoken_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if sec and not h:
        parts.append(f"{sec} second{'s' if sec != 1 else ''}")
    return " and ".join(parts) or "no time"


ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
        "nineteen"]
TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return TENS[t] + (f" {ONES[o]}" if o else "")
    if n < 1000:
        h, rest = divmod(n, 100)
        return f"{ONES[h]} hundred" + (f" {number_words(rest)}" if rest else "")
    raise ValueError("number too large")
