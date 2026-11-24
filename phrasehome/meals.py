"""Meal planner: a weekly plan you can edit on the dashboard or by voice, the week's ingredient
list (minus what's in the pantry) kept in sync with the shopping list, AI meal planning that
respects everyone's allergies and dislikes, and "what can I make with what we have?".

meals:
  slots: [dinner]                    # or [breakfast, lunch, dinner]
  staples: [salt, pepper, olive oil, water]   # never added to the shopping list
  auto_sync: true                    # keep the shopping list updated as the plan changes

People's diets come from each instance's `diet:` (allergies, dislikes, likes, style) plus anything
said to the AI ("Mia is allergic to peanuts"). The pantry is the shared list "pantry"; ticking an
item off the shopping list on the dashboard moves it into the pantry.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import clock

log = logging.getLogger("phrasehome.meals")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
UNITS = {"lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb", "oz": "oz", "ounce": "oz", "ounces": "oz",
         "g": "g", "gram": "g", "grams": "g", "kg": "kg", "cup": "cup", "cups": "cup", "tbsp": "tbsp",
         "tablespoon": "tbsp", "tablespoons": "tbsp", "tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp",
         "can": "can", "cans": "can", "clove": "clove", "cloves": "clove", "ml": "ml", "l": "l",
         "pack": "pack", "packs": "pack", "bunch": "bunch", "slice": "slice", "slices": "slice"}


def week_of(d: date | None = None) -> str:
    d = d or clock.now().date()
    return (d - timedelta(days=d.weekday())).isoformat()


def norm_item(name: str) -> str:
    n = re.sub(r"[^a-z ]", " ", name.lower())
    n = re.sub(r"\s+", " ", n).strip()
    words = n.split()
    last = words[-1] if words else ""
    if len(last) > 4 and last.endswith("ies"):
        words[-1] = last[:-3] + "y"              # berries -> berry
    elif len(last) > 4 and last.endswith("oes"):
        words[-1] = last[:-2]                    # tomatoes -> tomato
    elif len(last) > 3 and last.endswith("es") and last[-3] in "sxz":
        words[-1] = last[:-2]
    elif words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def parse_qty(q: str) -> tuple[Fraction | None, str]:
    """'1 1/2 cups' -> (3/2, 'cup'); '2' -> (2, ''); 'a pinch' -> (None, 'a pinch')."""
    q = (q or "").strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)?\s*(\d+/\d+)?\s*([a-z]*)\.?$", q)
    if not m or not (m.group(1) or m.group(2)):
        return None, q
    total = Fraction(0)
    if m.group(1):
        total += Fraction(m.group(1))
    if m.group(2):
        total += Fraction(m.group(2))
    return total, UNITS.get(m.group(3), m.group(3))


def fmt_qty(n: Fraction, unit: str) -> str:
    whole, rest = divmod(n.numerator, n.denominator)
    if rest == 0:
        s = str(whole)
    elif n.denominator in (2, 3, 4, 8):
        s = (f"{whole} " if whole else "") + f"{rest}/{n.denominator}"
    else:
        s = f"{float(n):.2f}".rstrip("0").rstrip(".")
    return f"{s} {unit}".strip()


class Meals:
    def __init__(self, home) -> None:
        self.home = home
        self.cfg = dict(home.config.extra.get("meals") or {})
        self.slots = list(self.cfg.get("slots", ["dinner"]))
        self.staples = {norm_item(s) for s in self.cfg.get("staples", ["salt", "pepper", "water", "olive oil"])}
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "meals.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {"weeks": {}}
        if "pantry" not in home.instances.shared_lists:
            home.instances.shared_lists.append("pantry")
        self._lock = threading.RLock()
        home.meals = self

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    # ---------- the plan ----------
    def week(self, week: str | None = None) -> dict[str, Any]:
        wk = week or week_of()
        with self._lock:
            w = self.data["weeks"].setdefault(wk, {"days": {}, "synced": []})
            return w

    def set_meal(self, day: str, slot: str, meal: dict | None, week: str | None = None) -> None:
        day = day.lower()[:3]
        if day not in DAYS:
            raise ValueError("day must be mon..sun")
        if slot not in self.slots:
            raise ValueError(f"slot must be one of {', '.join(self.slots)}")
        with self._lock:
            w = self.week(week)
            if meal and meal.get("name"):
                ings = []
                for ing in meal.get("ingredients") or []:
                    if isinstance(ing, str):
                        ing = self.parse_line(ing)
                    if ing.get("item"):
                        ings.append({"item": str(ing["item"]).strip(), "qty": str(ing.get("qty", "")).strip()})
                w["days"].setdefault(day, {})[slot] = {"name": str(meal["name"]).strip(), "ingredients": ings,
                                                       "servings": int(meal.get("servings") or 4),
                                                       "notes": str(meal.get("notes", ""))}
            else:
                w["days"].get(day, {}).pop(slot, None)
            self._save()
        if self.cfg.get("auto_sync", True):
            self.sync(week)

    @staticmethod
    def parse_line(line: str) -> dict[str, str]:
        """'1 1/2 cups rice' -> {'qty': '1 1/2 cups', 'item': 'rice'}; 'rice' -> {'item': 'rice'}"""
        m = re.match(r"^\s*((?:\d+(?:\.\d+)?\s*)?(?:\d+/\d+)?\s*(?:[a-zA-Z]+\.?)?)\s+(.+)$", line)
        if m and re.match(r"^\d", m.group(1).strip()):
            qty = m.group(1).strip()
            unit = qty.split()[-1].lower().rstrip(".")
            if not unit[0].isdigit() and unit not in UNITS:  # "2 eggs": no unit
                num = " ".join(qty.split()[:-1])
                return {"qty": num, "item": f"{qty.split()[-1]} {m.group(2)}".strip()}
            return {"qty": qty, "item": m.group(2).strip()}
        return {"qty": "", "item": line.strip()}

    def today(self, slot: str = "dinner") -> dict | None:
        d = clock.now().date()
        return self.week(week_of(d))["days"].get(DAYS[d.weekday()], {}).get(slot)

    # ---------- ingredients and the shopping list ----------
    def pantry(self) -> list[str]:
        return self.home.instances.list_get(self.home.instances.main, "pantry")

    def ingredients(self, week: str | None = None) -> list[dict[str, Any]]:
        """Everything the week's meals need, combined, with have=True for pantry/staple items."""
        w = self.week(week)
        pantry = {norm_item(p) for p in self.pantry()}
        combined: dict[str, dict[str, Any]] = {}
        for day in DAYS:
            for slot, meal in w["days"].get(day, {}).items():
                for ing in meal.get("ingredients", []):
                    key = norm_item(ing["item"])
                    if not key:
                        continue
                    e = combined.setdefault(key, {"item": ing["item"], "amounts": {}, "other": [], "meals": []})
                    e["meals"].append(meal["name"])
                    n, unit = parse_qty(ing.get("qty", ""))
                    if n is not None:
                        e["amounts"][unit] = e["amounts"].get(unit, Fraction(0)) + n
                    elif ing.get("qty"):
                        e["other"].append(ing["qty"])
        out = []
        for key, e in sorted(combined.items()):
            qty = " + ".join([fmt_qty(n, u) for u, n in e["amounts"].items()] + e["other"])
            have = key in self.staples or any(key == p or key in p or p in key for p in pantry if p)
            out.append({"item": e["item"], "key": key, "qty": qty, "have": have,
                        "meals": sorted(set(e["meals"]))})
        return out

    def sync(self, week: str | None = None) -> list[str]:
        """Put what the plan needs (and the pantry lacks) on the shopping list; take off what the
        plan no longer needs (only items this sync added)."""
        insts = self.home.instances
        with self._lock:
            w = self.week(week)
            needed = [f"{i['item']} ({i['qty']})" if i["qty"] else i["item"]
                      for i in self.ingredients(week) if not i["have"]]
            previous = set(w.get("synced", []))
            stale = [s for s in previous if s not in needed]
            if stale:
                insts.list_remove(insts.main, "shopping", stale)
            current = {x.lower() for x in insts.list_get(insts.main, "shopping")}
            fresh = [n for n in needed if n.lower() not in current]
            if fresh:
                insts.list_add(insts.main, "shopping", fresh)
            w["synced"] = needed
            self._save()
        return needed

    def bought(self, item: str) -> None:
        """Shopping list item ticked off: move it into the pantry."""
        insts = self.home.instances
        insts.list_remove(insts.main, "shopping", [item])
        insts.list_add(insts.main, "pantry", [re.sub(r"\s*\(.*\)$", "", item)])

    # ---------- diets ----------
    def diets(self) -> dict[str, dict]:
        insts = self.home.instances
        out = {}
        for i in insts.all.values():
            d = insts.diet(i, i.diet)
            if any(d.values()):
                out[i.name] = d
        return out

    # ---------- AI ----------
    PLAN_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["meals"],
                   "properties": {"meals": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False,
                       "required": ["day", "slot", "name", "servings", "ingredients"],
                       "properties": {
                           "day": {"type": "string", "enum": DAYS},
                           "slot": {"type": "string"},
                           "name": {"type": "string"},
                           "servings": {"type": "integer"},
                           "ingredients": {"type": "array", "items": {
                               "type": "object", "additionalProperties": False, "required": ["qty", "item"],
                               "properties": {"qty": {"type": "string"}, "item": {"type": "string"}}}}}}}}}

    def plan_week(self, request: str = "", week: str | None = None, days: list[str] | None = None,
                  keep_existing: bool = True) -> list[dict]:
        from . import ai

        w = self.week(week)
        days = [d.lower()[:3] for d in (days or DAYS)]
        existing = {f"{d} {s}": m["name"] for d in DAYS for s, m in w["days"].get(d, {}).items()}
        todo = [f"{d} {s}" for d in days for s in self.slots if not (keep_existing and f"{d} {s}" in existing)]
        if not todo:
            return []
        people = len([i for i in self.home.instances.all.values() if not i.main]) or 2
        prompt = (f"Plan home-cooked meals for these slots: {', '.join(todo)}.\n"
                  f"Household size: about {people} people. Diets (must respect allergies strictly, "
                  f"avoid dislikes): {json.dumps(self.diets()) or 'none given'}.\n"
                  f"Already planned this week (don't repeat): {json.dumps(existing)}.\n"
                  f"Pantry (use these where it makes sense): {', '.join(self.pantry()) or 'unknown'}.\n"
                  f"Requests: {request or 'varied, family-friendly, weeknight meals under 45 minutes'}.\n"
                  "Give every ingredient a quantity like '1 lb', '2 cups', '3' or '1 can', and use plain "
                  "grocery names (e.g. 'chicken thighs', 'yellow onion').")
        res = ai.ask(self.home, prompt, schema=self.PLAN_SCHEMA, effort="medium")
        planned = []
        for m in res["meals"]:
            if m["day"] in days and m["slot"] in self.slots:
                planned.append(m)
        with self._lock:
            auto = self.cfg.get("auto_sync", True)
            self.cfg["auto_sync"] = False  # sync once at the end
            try:
                for m in planned:
                    self.set_meal(m["day"], m["slot"], m, week)
            finally:
                self.cfg["auto_sync"] = auto
        if self.cfg.get("auto_sync", True):
            self.sync(week)
        return planned

    SUGGEST_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["ideas"],
                      "properties": {"ideas": {"type": "array", "items": {
                          "type": "object", "additionalProperties": False,
                          "required": ["name", "uses", "missing", "minutes"],
                          "properties": {"name": {"type": "string"},
                                         "uses": {"type": "array", "items": {"type": "string"}},
                                         "missing": {"type": "array", "items": {"type": "string"}},
                                         "minutes": {"type": "integer"}}}}}}

    def suggest(self, request: str = "") -> list[dict]:
        from . import ai

        prompt = (f"Suggest 3 meals we could cook mostly from this pantry: {', '.join(self.pantry()) or 'empty'}. "
                  f"Diets to respect: {json.dumps(self.diets()) or 'none'}. {request} "
                  "List what each uses from the pantry and the few things we'd be missing.")
        return ai.ask(self.home, prompt, schema=self.SUGGEST_SCHEMA)["ideas"]


def spoken_meal(meal: dict | None, when: str = "tonight") -> str:
    if not meal:
        return f"Nothing's planned for {when} yet."
    return f"{when[0].upper() + when[1:]} is {meal['name']}."
