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
