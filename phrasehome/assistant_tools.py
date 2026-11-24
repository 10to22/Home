"""More tools for the AI assistant, each available only when its feature is set up."""

from __future__ import annotations

import json
from typing import Any, Callable

from . import clock


def _t(name: str, description: str, props: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description, "strict": True,
            "input_schema": {"type": "object", "properties": props,
                             "required": list(props) if required is None else required,
                             "additionalProperties": False}}


S = {"type": "string"}
PERSON = {"type": "string", "description": "Whose (a person's name); empty for the person speaking."}


def tools_for(home, inst) -> list[dict]:
    kid = inst.kid_mode
    out = []
    if getattr(home, "cameras", None) and home.cameras.feeds:
        out.append(_t("look_at_camera", "See a camera's picture right now (front door, porch, yard, pets...).",
                      {"camera": {"type": "string", "enum": list(home.cameras.feeds)}, "question": S}))
    if getattr(home, "weather", None):
        out.append(_t("weather", "Forecast, air quality, pollen and weather alerts.",
                      {"day": {"type": "string", "enum": ["today", "tomorrow", "both"]}}))
    if getattr(home, "commute", None) and home.commute.people():
        out.append(_t("commute", "Traffic and when to leave for work or school.", {"person": PERSON}))
    if getattr(home, "energy", None) and home.energy.plugs:
        out.append(_t("energy", "Power use and cost by device and by person, prices, and the plugs "
                      "(appliances): status, on/off, or run when power is cheap.", {
                          "action": {"type": "string", "enum": ["report", "price", "status", "on", "off", "run_cheap"]},
                          "period": {"type": "string", "enum": ["today", "week", "month"]},
                          "plug": {"type": "string", "description": f"One of: {', '.join(home.energy.plugs)}; or empty."},
                          "hours": {"type": "number", "description": "For run_cheap: hours to leave it on, or 0."}}))
    if getattr(home, "meals", None):
        out.append(_t("meals", "The weekly meal plan: what's for dinner, set or clear a meal, plan the "
                      "week with AI, suggest meals from the pantry, the week's ingredients, sync to the shopping list.", {
                          "action": {"type": "string", "enum": ["today", "week", "set", "clear", "plan", "suggest", "ingredients", "sync"]},
                          "day": {"type": "string", "description": "mon..sun, or empty."},
                          "slot": {"type": "string", "description": f"One of {', '.join(home.meals.slots)}, or empty."},
                          "name": S, "ingredients": {"type": "array", "items": S, "description": "Lines like '1 lb chicken thighs'."},
                          "request": {"type": "string", "description": "Preferences for planning/suggestions."},
                          "next_week": {"type": "boolean"}}))
        out.append(_t("diet", "Allergies, dislikes, likes or diet style for someone (used by the meal planner).", {
            "person": PERSON, "key": {"type": "string", "enum": ["allergies", "dislikes", "likes", "style"]},
            "add": {"type": "array", "items": S}, "remove": {"type": "array", "items": S}}))
    if getattr(home, "packages", None):
        out.append(_t("packages", "Package tracking: list, add a tracking number, or remove.", {
            "action": {"type": "string", "enum": ["list", "add", "remove"]}, "number": S, "name": S}))
    out.append(_t("countdowns", "Countdowns to dates (trips, birthdays, holidays).", {
        "action": {"type": "string", "enum": ["list", "add", "remove"]}, "name": S,
        "date": {"type": "string", "description": "YYYY-MM-DD for add, else empty."}, "person": PERSON}))
    if getattr(home, "pets", None):
        out.append(_t("pets", "Pets: log a feeding, run the feeder, or ask if they've been fed.", {
            "action": {"type": "string", "enum": ["fed", "feed", "status"]}, "pet": S}))
    if getattr(home, "spotify", None):
        out.append(_t("music", "Spotify on the house speakers.", {
            "action": {"type": "string", "enum": ["play", "pause", "resume", "next", "previous", "like", "what", "volume", "shuffle"]},
            "query": {"type": "string", "description": "Song, artist, album, playlist or genre to play."},
            "kind": {"type": "string", "enum": ["any", "track", "artist", "album", "playlist"]},
            "rooms": {"type": "array", "items": S, "description": "Rooms to play in, or empty for everywhere."},
            "level": {"type": "integer", "description": "Volume 0-100 for volume."}}))
    if getattr(home, "rooms", None):
        out.append(_t("where_is", "Where a person, pet or tagged thing (keys, wallet...) is in the house.",
                      {"what": S}))
        if home.rooms.beds:
            out.append(_t("sleep", "How someone slept, from their bed sensor.", {"person": PERSON}))
    out.append(_t("check_closed", "Are all doors and windows closed and locked?", {}))
    if not kid:
        if getattr(home, "alarm", None):
            out.append(_t("alarm_system", "The home alarm: arm, disarm, or status.",
                          {"action": {"type": "string", "enum": ["arm", "disarm", "status"]}}))
        out.append(_t("customize", "Make new voice commands or schedules from existing commands/routines, "
                      "list them, or remove one. Steps run in order.", {
                          "action": {"type": "string", "enum": ["add_command", "add_schedule", "list", "remove"]},
                          "name": S,
                          "phrases": {"type": "array", "items": S, "description": "For add_command: what people will say."},
                          "at": {"type": "string", "description": "For add_schedule: HH:MM, sunrise or sunset."},
                          "days": {"type": "string", "description": "daily, weekdays, weekends, or mon,wed,fri."},
                          "steps": {"type": "array", "items": {
                              "type": "object", "additionalProperties": False, "required": ["command", "routine", "slots", "say"],
                              "properties": {"command": {"type": "string", "description": "An existing command name, or empty."},
                                             "routine": {"type": "string", "description": "An existing routine name, or empty."},
                                             "slots": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                                                    "required": ["name", "value"],
                                                                                    "properties": {"name": S, "value": S}}},
                                             "say": {"type": "string", "description": "Something to say, or empty."}}}}}))
    if getattr(home, "diary", None):
        out.append(_t("diary", "The house's diary for a day (what happened).",
                      {"date": {"type": "string", "description": "YYYY-MM-DD, or empty for today."}}))
    return out


def run(home, name: str, args: dict, room: str | None, inst, person: str | None = None) -> Any:
    """Returns text, or a list of content blocks (camera pictures)."""
    fn: Callable | None = HANDLERS.get(name)
    if fn is None:
        raise KeyError(name)
    return fn(home, args, room, inst)


def _target(home, inst, who: str):
    return home.instances.get(who, asker=inst)


def _look(home, a, room, inst):
    from .ai import image_block

    jpeg = home.cameras.snapshot(a["camera"])
    return [image_block(jpeg), {"type": "text", "text": f"The {a['camera']} camera, {clock.spoken_time(clock.now())}. "
                                                       "Describe only what's visible; don't identify strangers."}]


def _weather(home, a, room, inst):
    w = home.weather
    day = a.get("day", "today")
    parts = [w.summary(0)] if day in ("today", "both") else []
    if day in ("tomorrow", "both"):
        parts.append(w.summary(1))
    need = w.needs()
    if need and day != "tomorrow":
        parts.append("Take: " + ", ".join(need) + ".")
    return " ".join(parts)


def _commute(home, a, room, inst):
    target = _target(home, inst, a.get("person", ""))
    if target.main:
        return " ".join(f"{i.name}: {home.commute.summary(i)}" for i in home.commute.people())
    return home.commute.summary(target)


def _energy(home, a, room, inst):
    e = home.energy
    act = a["action"]
    if act == "report":
        days = {"today": 1, "week": 7, "month": 30}.get(a.get("period") or "month", 30)
        t = e.totals(days)
        t["people"] = {e.person_name(k): round(v, 2) for k, v in t["people"].items()}
        return e.report(days) + " Details: " + json.dumps({k: (round(v, 2) if isinstance(v, float) else v) for k, v in t.items()})
    if act == "price":
        from .reports import REPORTS

        return REPORTS["price"](home, {})
    if act == "status":
        return json.dumps({n: {"watts": p.power, "on": p.on, "state": p.state} for n, p in e.plugs.items()})
    if act == "run_cheap":
        return e.run_when_cheap(a["plug"], a.get("hours") or None)
    e.plug(a["plug"]).switch(act == "on")
    return f"{a['plug']} {act}"


def _meals(home, a, room, inst):
    from .meals import DAYS, spoken_meal, week_of

    m = home.meals
    wk = week_of(clock.now().date() + __import__("datetime").timedelta(days=7)) if a.get("next_week") else None
    act = a["action"]
    slot = a.get("slot") or m.slots[-1]
    day = (a.get("day") or DAYS[clock.now().weekday()]).lower()[:3]
    if act == "today":
        return spoken_meal(m.today(slot))
    if act == "week":
        w = m.week(wk)
        return json.dumps({d: {s: x["name"] for s, x in w["days"].get(d, {}).items()} for d in DAYS})
    if act == "set":
        m.set_meal(day, slot, {"name": a["name"], "ingredients": a.get("ingredients") or []}, wk)
        return f"set {day} {slot} to {a['name']}; shopping list updated"
    if act == "clear":
        m.set_meal(day, slot, None, wk)
        return f"cleared {day} {slot}"
    if act == "plan":
        planned = m.plan_week(a.get("request", ""), wk)
        return "planned: " + "; ".join(f"{p['day']} {p['name']}" for p in planned) + ". Shopping list updated."
    if act == "suggest":
        return json.dumps(m.suggest(a.get("request", "")))
    if act == "ingredients":
        return json.dumps([{k: i[k] for k in ("item", "qty", "have")} for i in m.ingredients(wk)])
    if act == "sync":
        return f"shopping list now has {len(m.sync(wk))} items from the plan"
    raise ValueError(act)


def _diet(home, a, room, inst):
    target = _target(home, inst, a.get("person", ""))
    return json.dumps(home.instances.diet_update(target, a["key"], a.get("add") or [], a.get("remove") or []))


def _packages(home, a, room, inst):
    p = home.packages
    if a["action"] == "add":
        it = p.add(a["number"], a.get("name", ""), inst.id)
        return f"tracking {it['name']}"
    if a["action"] == "remove":
        return f"removed {p.remove(a.get('name') or a.get('number'))}"
    return p.report()


def _countdowns(home, a, room, inst):
    target = _target(home, inst, a.get("person", ""))
    insts = home.instances
    if a["action"] == "add":
        insts.countdown_add(target, a["name"], a["date"])
        return f"added countdown to {a['name']}"
    if a["action"] == "remove":
        return f"removed {insts.countdown_remove(target, a['name'])}"
    return json.dumps(insts.countdowns(target))


def _pets(home, a, room, inst):
    p = home.pets
    by = "" if inst.main else inst.id
    act = a["action"]
    return p.fed(a.get("pet", ""), by) if act == "fed" else p.feed(a.get("pet", ""), by) if act == "feed" \
        else p.status(a.get("pet", ""))


def _music(home, a, room, inst):
    sp = home.spotify
    if a["action"] == "play":
        rooms = [r.lower() for r in a.get("rooms") or []] or None
        return sp.play(a.get("query", ""), a.get("kind") or "any", rooms)
    return sp.simple(a["action"], a.get("level"))


def _where(home, a, room, inst):
    from .reports import REPORTS

    return REPORTS["where"](home, {"what": a["what"]})


def _sleep(home, a, room, inst):
    target = _target(home, inst, a.get("person", ""))
    return home.rooms.sleep_report(target.id)


def _closed(home, a, room, inst):
    from .reports import REPORTS

    return REPORTS["closed"](home, {})


def _alarm(home, a, room, inst):
    return home.run_action({"backend": "security", "security": a["action"]}, {"here": room} if room else {})


def _customize(home, a, room, inst):
    c = home.customs
    act = a["action"]
    steps = [{k: v for k, v in s.items() if v} for s in a.get("steps") or []]
    if act == "add_command":
        e = c.add_command(a["name"], a.get("phrases") or [], steps)
        return f"added command {e['name']}: say {', '.join(e['phrases'])}"
    if act == "add_schedule":
        e = c.add_schedule(a["name"], a["at"], a.get("days") or "daily", steps)
        return f"added schedule {e['name']} at {e['at']} ({e['days']})"
    if act == "remove":
        return f"removed {c.remove(a['name'])}"
    return json.dumps(c.list())


def _diary(home, a, room, inst):
    text = home.diary.read(a.get("date") or None)
    if not text:
        events = home.events.query(limit=60)
        from .diary import notable

        return "No diary entry for that day yet. Today's events so far: " + json.dumps(notable(events))[:8000]
    return text


HANDLERS: dict[str, Callable] = {
    "look_at_camera": _look, "weather": _weather, "commute": _commute, "energy": _energy,
    "meals": _meals, "diet": _diet, "packages": _packages, "countdowns": _countdowns, "pets": _pets,
    "music": _music, "where_is": _where, "sleep": _sleep, "check_closed": _closed,
    "alarm_system": _alarm, "customize": _customize, "diary": _diary,
}
