"""Read-only calendars: an instance's `calendar:` is an iCal (.ics) link, e.g. Google Calendar's
"Secret address in iCal format". Used by the AI assistant ("what's on my calendar tomorrow?")
and briefings. Recurring events need `pip install recurring-ical-events`; without it, only
one-off events are read.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any

import requests

log = logging.getLogger("phrasehome.calendar")
_cache: dict[str, tuple[float, bytes]] = {}


def _fetch(url: str) -> bytes:
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < 600:
        return hit[1]
    data = requests.get(url, timeout=10).content
    _cache[url] = (time.monotonic(), data)
    return data


def events(url: str, start: datetime, end: datetime, ics: bytes | None = None) -> list[dict[str, Any]]:
    data = ics if ics is not None else _fetch(url)
    try:
        import icalendar
        import recurring_ical_events

        cal = icalendar.Calendar.from_ical(data)
        out = []
        for ev in recurring_ical_events.of(cal).between(start, end):
            out.append(_row(ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None,
                            str(ev.get("SUMMARY", "")), str(ev.get("LOCATION", "") or "")))
        return sorted(out, key=lambda e: e["start"])
    except ImportError:
        return _simple(data.decode("utf-8", "replace"), start, end)


def _row(s, e, summary: str, location: str = "") -> dict[str, Any]:
    all_day = isinstance(s, date) and not isinstance(s, datetime)
    fmt = (lambda d: d.isoformat()) if all_day else (lambda d: d.astimezone().strftime("%Y-%m-%d %H:%M"))
    return {"start": fmt(s), "end": fmt(e) if e else "", "summary": summary, "all_day": all_day,
            "location": location}


def _parse_dt(value: str):
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    utc = value.endswith("Z")
    dt = datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        from datetime import timezone

        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()  # floating/local times: treat as the hub's timezone


def _simple(text: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    text = re.sub(r"\r?\n[ \t]", "", text)  # unfold lines
    out = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        fields: dict[str, str] = {}
        for line in block.split("END:VEVENT")[0].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fields.setdefault(k.split(";")[0].upper(), v)
        if "DTSTART" not in fields or "RRULE" in fields:
            continue
        s = _parse_dt(fields["DTSTART"])
        e = _parse_dt(fields["DTEND"]) if "DTEND" in fields else None
        s_cmp = datetime(s.year, s.month, s.day).astimezone() if not isinstance(s, datetime) else s
        if start <= s_cmp < end:
            out.append(_row(s, e, fields.get("SUMMARY", "").replace("\\,", ","), fields.get("LOCATION", "")))
    return sorted(out, key=lambda e: e["start"])


def upcoming(url: str, days: int = 1, from_day: int = 0) -> list[dict[str, Any]]:
    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today + timedelta(days=from_day)
    return events(url, start, start + timedelta(days=days))
