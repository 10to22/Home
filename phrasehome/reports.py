"""Spoken status reports: {report: closed | weather | climate | alarm | ...}.
Each returns a sentence; other modules register more kinds with `register()`."""

from __future__ import annotations

from typing import Callable

REPORTS: dict[str, Callable] = {}


def register(name: str):
    def deco(fn):
        REPORTS[name] = fn
        return fn
    return deco


def openings(home) -> list[dict]:
    """Doors, windows and gates with a contact sensor: configured ones, plus any input named like
    contact / window / door / gate that a device node reports."""
    alarm = home.config.extra.get("alarm") or {}
    out = [dict(o) for o in alarm.get("openings", [])]
    known = {(o["node"], o["input"]) for o in out}
    for d, cfg in home.config.doors.items():
        c = cfg.get("contact")
        if c and (c["node"], c["input"]) not in known:
            out.append({"name": f"{d} door", **c})
            known.add((c["node"], c["input"]))
    if home.sensors:
        for (node, inp), val in list(home.sensors.values.items()):
            if (node, inp) in known or "state" not in val:
                continue
            if any(w in inp for w in ("contact", "window", "door", "gate")):
                out.append({"name": f"{node} {inp}", "node": node, "input": inp})
    return out


def open_things(home) -> tuple[list[str], list[str], list[str]]:
    """(open openings, unlocked doors, unknown) right now."""
    opened, unknown = [], []
    for o in openings(home):
        cur = home.sensors.current(o["node"], o["input"]) if home.sensors else None
        if cur is None:
            unknown.append(o["name"])
        elif cur.get("state") != str(o.get("closed_when", "on")).lower():
            opened.append(o["name"])
    unlocked = [f"the {d} door" for d, st in home.doors.states().items() if st == "unlocked"]
    return opened, unlocked, unknown


@register("closed")
def report_closed(home, action) -> str:
    opened, unlocked, unknown = open_things(home)
    if not opened and not unlocked:
        if action.get("only_problems"):
            return ""
        return "Everything is closed and locked." + (f" I can't check {', '.join(unknown)}." if unknown else "")
    parts = []
    if opened:
        parts.append(_join(opened) + (" is open" if len(opened) == 1 else " are open"))
    if unlocked:
        parts.append(_join(unlocked) + (" is unlocked" if len(unlocked) == 1 else " are unlocked"))
    return "Heads up: " + ", and ".join(parts) + "."


@register("weather")
def report_weather(home, action) -> str:
    w = getattr(home, "weather", None)
    if w is None:
        return "Weather isn't set up yet."
    day = str(action.get("day", "today")).lower()
    return w.summary(1 if day == "tomorrow" else 0)


@register("alarm")
def report_alarm(home, action) -> str:
    a = getattr(home, "alarm", None)
    return f"The alarm is {a.state}." if a else "The alarm isn't set up."


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


@register("energy")
def report_energy(home, action) -> str:
    e = getattr(home, "energy", None)
    if e is None:
        return "Energy monitoring isn't set up."
    period = str(action.get("period", "month")).lower()
    return e.report({"today": 1, "week": 7, "month": 30}.get(period, 30))


@register("price")
def report_price(home, action) -> str:
    e = getattr(home, "energy", None)
    if e is None:
        return "Energy prices aren't set up."
    from . import clock

    price, cheap = e.price_at(clock.now())
    text = f"Power costs {e.currency}{price:.2f} per kilowatt hour right now" + (", which is the cheap rate." if cheap else ".")
    nxt = e.next_cheap()
    if nxt and not cheap:
        text += f" It gets cheaper at {clock.spoken_time(nxt)}."
    return text


@register("packages")
def report_packages(home, action) -> str:
    p = getattr(home, "packages", None)
    return p.report() if p else "Package tracking isn't set up."


@register("countdowns")
def report_countdowns(home, action) -> str:
    inst = home.instances.for_room(action.get("_context", {}).get("here"))
    items = home.instances.countdowns(inst)
    if not items:
        return "There are no countdowns."
    return " ".join(f"{c['days']} day{'s' if c['days'] != 1 else ''} until {c['name']}." if c["days"] > 0
                    else f"{c['name']} is today!" for c in items[:4])


@register("dinner")
def report_dinner(home, action) -> str:
    m = getattr(home, "meals", None)
    if m is None:
        return "The meal planner isn't set up."
    from .meals import spoken_meal

    slot = action.get("slot", "dinner")
    return spoken_meal(m.today(slot), "tonight" if slot == "dinner" else f"{slot} today")


@register("where")
def report_where(home, action) -> str:
    """{report: where, what: keys}: Bluetooth tags, or people ("where is Sam")."""
    what = str(action.get("what", "")).strip()
    rooms = getattr(home, "rooms", None)
    if rooms is None:
        return "Room tracking isn't set up."
    who = rooms._person_for_name(what)
    if who:
        w = rooms.where(who)
        name = home.instances.all[who].name
        return f"{name} is in the {w[0]}." if w else f"I'm not sure where {name} is."
    return rooms.locator.say_where(what)


@register("sleep")
def report_sleep(home, action) -> str:
    rooms = getattr(home, "rooms", None)
    if rooms is None:
        return "Bed sensors aren't set up."
    inst = home.instances.for_room(action.get("_context", {}).get("here"))
    person = action.get("person") or (inst.id if not inst.main else next(iter(rooms.beds), ""))
    return rooms.sleep_report(person)


@register("commute")
def report_commute(home, action) -> str:
    c = getattr(home, "commute", None)
    if c is None:
        return "Commute info isn't set up."
    room = action.get("_context", {}).get("here")
    inst = home.instances.for_room(room)
    if action.get("person"):
        inst = home.instances.get(action["person"], asker=home.instances.main)
    if inst.main:
        people = c.people()
        return " ".join(f"{i.name}: {c.summary(i)}" for i in people) or "Nobody has a commute set up."
    return c.summary(inst)


@register("pet")
def report_pet(home, action) -> str:
    p = getattr(home, "pets", None)
    if p is None:
        return "No pets are set up."
    return p.status(str(action.get("pet", "")))


@register("climate")
def report_climate(home, action) -> str:
    """{report: climate, room: "{here}"} or all rooms: temperature and humidity."""
    from .web import climate

    c = climate(home)
    if not c:
        return "I don't have temperature sensors yet."
    room = str(action.get("room") or action.get("_context", {}).get("here") or "").lower()
    if room and room in c:
        v = c[room]
        parts = [f"{v['temperature']:.0f} degrees"] if "temperature" in v else []
        if "humidity" in v:
            parts.append(f"{v['humidity']} percent humidity")
        return f"It's {' and '.join(parts)} in the {room}."
    temps = {r: v["temperature"] for r, v in c.items() if "temperature" in v}
    if not temps:
        return "I don't have temperature readings."
    cold, warm = min(temps, key=temps.get), max(temps, key=temps.get)
    return (f"The coldest room is the {cold} at {temps[cold]:.0f} degrees, and the warmest is the "
            f"{warm} at {temps[warm]:.0f}.")


# ---------------------------------------------------------------- answers that never need the AI
def _names(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


@register("home")
def report_home(home, action) -> str:
    """{report: home}: who's in and who's out, from presence tracking (instant, works offline)."""
    people = [i for i in home.instances.all.values() if not i.main]
    pres = home.state.presence
    if not people:
        return "Nobody's set up as living here yet."
    if not pres:
        return "I can't tell who's home yet: nothing's tracking it. Add your phones under presence: in config.yaml."
    here = set(home.state.home_people())
    inn = [i for i in people if i.id in here]
    out = [i.name for i in people if i.id in pres and i.id not in here]
    unknown = [i.name for i in people if i.id not in pres]
    rooms = getattr(home, "rooms", None)
    where = []
    for i in inn:
        try:
            w = rooms.where(i.id) if rooms is not None else None
        except Exception:
            w = None
        where.append(f"{i.name} in the {w[0]}" if w else i.name)
    if inn and not out and not unknown:
        bits = ["Everyone's home" + (f": {_names(where)}" if any(" in the " in w for w in where) else "")]
    elif not inn:
        bits = ["Nobody's home"]
    else:
        bits = [f"{_names(where)} {'is' if len(inn) == 1 else 'are'} home"]
        if out:
            bits.append(f"{_names(out)} {'is' if len(out) == 1 else 'are'} out")
    if unknown and inn:
        bits.append(f"not sure about {_names(unknown)}")
    return ". ".join(b[0].upper() + b[1:] for b in bits) + "."


@register("today")
def report_today(home, action) -> str:
    """{report: today}: the asker's reminders, calendar, exams and deadlines, the house calendar, a house
    meeting and the bins, all for today (instant, works offline; reads a calendar link if they have one)."""
    from datetime import datetime, timedelta

    from . import clock

    ctx = action.get("_context") or {}
    person = action.get("person") or ctx.get("person")
    inst = home.instances.all.get(person) if person in home.instances.all else home.instances.for_room(ctx.get("here"))
    now = clock.now()
    end = now.replace(hour=23, minute=59, second=59)
    t = clock.spoken_time
    bits: list[tuple[datetime, str]] = []

    def when(at: datetime) -> datetime:
        return at if at.tzinfo else at.astimezone()

    for i in home.instances.items(inst):
        try:
            at = when(datetime.fromisoformat(i["due"]))
        except (KeyError, ValueError):
            continue
        if now <= at <= end and i.get("kind") in ("reminder", "alarm", "event", "timer"):
            what = (i.get("text") or i.get("label") or i["kind"]).strip()
            bits.append((at, f"{what} at {t(at)}" if i["kind"] != "alarm" else f"an alarm at {t(at)}"))
    if getattr(inst, "calendar", None):
        try:
            from .calendar_feed import upcoming

            for e in upcoming(inst.calendar, days=1):
                if e.get("all_day"):
                    bits.append((now, str(e["summary"])))
                    continue
                at = datetime.strptime(e["start"][:16], "%Y-%m-%d %H:%M").astimezone()
                if at >= now - timedelta(minutes=10):
                    bits.append((at, f"{e['summary']} at {t(at)}"))
        except Exception:
            pass  # offline: the rest still works
    life = getattr(home, "life", None)
    if life is not None:
        try:
            for e in life.calendar.events(days=1, include_past_hours=0):
                at = when(datetime.fromisoformat(e["start"]))
                if at <= end:
                    bits.append((at, f"{e.get('what') or e.get('title') or 'something on the house calendar'} at {t(at)}"))
        except Exception:
            pass
        try:
            for who, ex in life.exams.all(2):
                if who is not None and who.id == inst.id:
                    day = "today" if ex["at"].date() == now.date() else "tomorrow"
                    bits.append((ex["at"], f"your {ex['subject']} exam {day} at {t(ex['at'])}"))
        except Exception:
            pass
        try:
            for who, d in life.deadlines.all(2):
                if who.id == inst.id:
                    at = when(datetime.fromisoformat(d["due"]))
                    day = "today" if at.date() == now.date() else "tomorrow"
                    bits.append((at, f"{d['what']} due {day} at {t(at)}"))
        except Exception:
            pass
        try:
            b = life.meeting.booked()
            if b:
                at = when(datetime.fromisoformat(b["at"]))
                if now.date() == at.date() and at >= now:
                    bits.append((at, f"the house meeting at {t(at)} in the {b['room']}"))
        except Exception:
            pass
        try:
            bins = life.bins
            if bins.cfg and clock.day_matches(str(bins.cfg.get("night", "thu")), now.date()):
                turn = bins.turn(now)
                if turn is not None and turn.id == inst.id:
                    bits.append((end, "it's your turn to take the bins out tonight"))
        except Exception:
            pass
    if not bits:
        return "Nothing on for you today." if not inst.main else "Nothing on today."
    seen, items = set(), []
    for _, b in sorted(bits, key=lambda x: x[0]):
        if b.lower() not in seen:
            seen.add(b.lower())
            items.append(b)
    head = "Today" if inst.main else f"Today for {inst.name}"
    return f"{head}: {_names(items[:6])}" + (f", and {len(items) - 6} more" if len(items) > 6 else "") + "."
