"""What the house knows about each housemate, shared with the whole house.

    "house, Parker's a vegetarian"            -> Parker is vegetarian (also their diet: meal plans,
                                                 recipes, party food and every AI answer respect it)
    "house, remember Jo's allergic to peanuts" -> allergy (kept out of meal plans)
    "house, Alex doesn't eat pork"             -> dislike, for meals
    "house, Finn hates coriander", "Sky plays bass", "remember that Jo works Saturdays"
    "house, I'm vegan" / "I'm allergic to shellfish" (from your own room, or when it knows your voice)
    "house, what do you know about Parker?"   "house, forget that Finn hates coriander"
    "house, Parker isn't vegetarian anymore"

Anyone can tell the house about anyone (you all live together); the AI gets these with every
question, and can add more itself. Start people off in config.yaml:

    instances:
      parker:
        name: Parker
        rooms: [parker's room]
        about: [vegetarian, studies law, hates loud music before 10am]

Saved in data/people.json. The dashboard's People card shows and edits them.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from . import clock

DIETS = {
    "vegetarian": "vegetarian", "veggie": "vegetarian", "vego": "vegetarian", "vegan": "vegan",
    "plant based": "vegan", "plant-based": "vegan", "pescatarian": "pescatarian", "pescetarian": "pescatarian",
    "halal": "halal", "kosher": "kosher", "gluten free": "gluten free", "gluten-free": "gluten free",
    "coeliac": "gluten free", "celiac": "gluten free", "dairy free": "dairy free", "dairy-free": "dairy free",
    "lactose intolerant": "lactose free", "keto": "keto", "on keto": "keto",
}
_DIET_RX = "|".join(sorted((re.escape(k) for k in DIETS), key=len, reverse=True))
_IS = r"(?:'s|’s| is| has gone| went| has become| became| has been| is now|'s now| are| am|'m)"
_NOT = r"(?:'s not|’s not| isn't| is not| isn’t| aren't|'m not| am not| no longer| not)"
_LEAD = r"^(?:(?:hey )?(?:house|home)[, ]+)?(?:just )?(?:remember|note|fyi|for your information|heads up|" \
        r"just so you know|so you know|keep in mind|don't forget)?(?: that)?[, ]*"


class People:
    def __init__(self, home) -> None:
        self.home = home
        self.path = Path(home.config.resolve_path(home.config.data_dir)) / "people.json"
        self._lock = threading.Lock()
        self._data: dict[str, list[dict]] | None = None

    # ---------- storage ----------
    def _load(self) -> dict[str, list[dict]]:
        if self._data is None:
            try:
                self._data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=1))
        tmp.replace(self.path)

    def housemates(self) -> list:
        insts = self.home.instances.all.values()
        people = [i for i in insts if not i.main]
        return people or list(insts)

    def find(self, name: str):
        n = (name or "").strip().lower()
        if n.endswith("'s"):
            n = n[:-2]
        return next((i for i in self.home.instances.all.values() if i.matches_person(n)), None)

    def facts(self, inst) -> list[str]:
        """Everything known about one person: config `about:` first, then what was said."""
        about = inst.extra.get("about") or []
        about = [about] if isinstance(about, str) else list(about)
        with self._lock:
            said = [f["text"] for f in self._load().get(inst.id, [])]
        out: list[str] = []
        for f in [str(a) for a in about] + said:
            if f.lower() not in (x.lower() for x in out):
                out.append(f)
        return out

    def add(self, inst, fact: str, by: str | None = None) -> dict:
        """Save a fact ("is vegetarian", "hates coriander"). Food facts also go into their diet."""
        fact = re.sub(r"\s+", " ", fact.strip().rstrip(".!")).strip()
        if not fact:
            raise ValueError("nothing to remember")
        with self._lock:
            mine = self._load().setdefault(inst.id, [])
            mine[:] = [f for f in mine if f["text"].lower() != fact.lower()]
            item = {"text": fact, "by": by or "", "time": clock.now().strftime("%Y-%m-%d")}
            mine.append(item)
            del mine[:-40]
            self._save()
        self._diet_from(inst, fact)
        self.home.events.log("people.fact", person=inst.id, text=fact)
        return item

    def forget(self, inst, words: str) -> int:
        w = (words or "").lower().strip()
        with self._lock:
            mine = self._load().get(inst.id, [])
            keep = [f for f in mine if w and w not in f["text"].lower()]
            n = len(mine) - len(keep)
            mine[:] = keep
            self._save()
        diet = self.home.instances.diet(inst)
        for d in set(DIETS.values()):
            if d in w and d in str(diet.get("style", "")):
                style = ", ".join(s for s in str(diet["style"]).split(", ") if s and s != d)
                self.home.instances.diet_update(inst, "style", [style] if style else [])
                n = max(n, 1)
        for key in ("allergies", "dislikes", "likes"):
            gone = [x for x in diet.get(key, []) if x in w]
            if gone:
                self.home.instances.diet_update(inst, key, remove=gone)
                n = max(n, 1)
        return n

    def _diet_from(self, inst, fact: str) -> None:
        f = fact.lower()
        insts = self.home.instances
        m = re.search(rf"\b({_DIET_RX})\b", f)
        if m and not re.search(r"\b(not|isn't|no longer|never)\b", f):
            style = DIETS[m.group(1)]
            cur = [s for s in str(insts.diet(inst).get("style", "")).split(", ") if s]
            if style not in cur:
                insts.diet_update(inst, "style", [", ".join(cur + [style])])
        m = re.search(r"allergic to (.+)$|(\w[\w ]*?) allergy$", f)
        if m:
            insts.diet_update(inst, "allergies", _items(m.group(1) or m.group(2)))
        m = re.search(r"(?:doesn't|does not|don't|won't|can't|cannot|never) eats? (.+)$", f)
        if m:
            insts.diet_update(inst, "dislikes", _items(m.group(1)))

    # ---------- for the AI and the dashboard ----------
    def all(self) -> dict[str, list[str]]:
        return {i.name: self.facts(i) for i in self.housemates()}

    def context_line(self) -> str:
        bits = [f"{n}: {'; '.join(fs)}" for n, fs in self.all().items() if fs]
        return ("What the house knows about the people who live here (respect it in every answer: food, "
                "plans, timing): " + " | ".join(bits) + ".") if bits else ""

    def about(self, inst) -> str:
        facts = self.facts(inst)
        diet = self.home.instances.diet(inst, inst.diet)
        extra = []
        if diet.get("allergies") and not any("allerg" in f.lower() for f in facts):
            extra.append("allergic to " + _join(diet["allergies"]))
        return _join(facts + extra)

    # ---------- what people say ----------
    def heard(self, said: str, speaker=None) -> tuple[str, dict] | None:
        """A sentence telling the house about someone, or asking what it knows. Returns the voice line
        and its values (already done), or None if it isn't that kind of sentence."""
        s = re.sub(r"\s+", " ", said.lower().replace("’", "'")).strip(" .!?")
        names = sorted({n for i in self.home.instances.all.values() if not i.main
                        for n in [i.id, i.name.lower(), *[p.lower() for p in i.people]]}, key=len, reverse=True)
        who = "|".join(re.escape(n) for n in names) or r"(?!x)x"
        m = re.match(rf"^(?:what do you know|what do we know|tell me) about ({who})$", s)
        if m:
            inst = self.find(m.group(1))
            text = self.about(inst)
            return ("people_about", {"name": inst.name, "facts": text}) if text else \
                ("people_nothing", {"name": inst.name})
        m = re.match(rf"^forget (?:that )?({who}){_IS}? (.+)$", s) or \
            re.match(rf"^({who}){_NOT} (.+?)(?: anymore| any more| now)?$", s)
        if m:
            inst = self.find(m.group(1))
            what = re.sub(r"^(?:a|an) ", "", m.group(2))
            known = " | ".join(self.facts(inst)).lower()
            if not (s.startswith("forget") or what in DIETS or what in known):
                return None  # "Parker isn't home" is about right now, not a fact
            self.forget(inst, DIETS.get(what, what))
            return "people_forgot", {"name": inst.name}
        if re.match(rf"^({who}) eats meat(?: now| again)?$", s):
            inst = self.find(s.split()[0])
            for d in ("vegetarian", "vegan", "pescatarian"):
                self.forget(inst, d)
            return "people_forgot", {"name": inst.name}
        body = re.sub(_LEAD, "", s, count=1).strip()
        me = speaker if speaker is not None and not getattr(speaker, "main", False) else None
        m = re.match(rf"^({who})({_IS}|'s| has| loves| likes| hates| can't stand| doesn't| does not| won't| plays|"
                     rf" studies| works| goes| needs| wants| prefers| always| never| only| drinks| eats) (.+)$", body)
        if m:
            inst, verb, rest = self.find(m.group(1)), m.group(2).strip(), m.group(3)
        elif me is not None and re.match(r"^(i'm|i am|i have|i've got|i hate|i love|i don't eat|i can't eat) ", body):
            m = re.match(r"^(i'm|i am|i have|i've got|i hate|i love|i don't eat|i can't eat) (.+)$", body)
            inst, rest = me, m.group(2)
            verb = {"i'm": "is", "i am": "is", "i have": "has", "i've got": "has", "i hate": "hates",
                    "i love": "loves", "i don't eat": "doesn't eat", "i can't eat": "can't eat"}[m.group(1)]
            if verb == "is" and not (re.match(rf"^(?:a |an )?({_DIET_RX})\b", rest) or rest.startswith("allergic")):
                return None  # "I'm tired" isn't a fact worth keeping
        else:
            return None
        verb = {"'s": "is", "is": "is", "are": "is", "am": "is", "'m": "is"}.get(verb, verb)
        if verb == "'s" or verb.startswith("'s"):
            verb = "is"
        dm = re.match(rf"^(?:a |an |now |now a )?({_DIET_RX})$", rest)
        by = getattr(speaker, "id", None)
        if dm:
            diet = DIETS[dm.group(1)]
            self.add(inst, f"is {diet}", by)
            return "people_saved_diet", {"name": inst.name, "diet": diet}
        am = re.match(r"^allergic to (.+)$", rest) if verb == "is" else None
        if am:
            self.add(inst, f"is allergic to {am.group(1)}", by)
            return "people_saved_allergy", {"name": inst.name, "what": am.group(1)}
        if not explicit_fact(s, verb, rest):
            return None
        fact = f"{verb} {rest}".strip()
        self.add(inst, fact, by)
        return "people_saved", {"name": inst.name, "fact": fact}


def explicit_fact(s: str, verb: str, rest: str) -> bool:
    """Only keep things clearly said as a fact about someone (not "Finn's room is a mess" or questions)."""
    if s.startswith(("what", "who", "where", "when", "why", "how", "is ", "are ", "does ", "do ", "can ")):
        return False
    if verb == "is" and re.match(r"^(home|out|away|here|there|in|at|on|coming|going|back|asleep|awake|"
                                 r"late|running|leaving|on the way|in the|at the)\b", rest):
        return False  # where someone is right now: presence's job, not a fact to keep
    if re.match(r"^(room|door|phone|car|stuff|turn|bed|keys)\b", rest):
        return False
    if re.search(r"\b(today|tonight|tomorrow|this (?:morning|afternoon|evening|week|weekend)|next week|right now|"
                 r"at the moment|on (?:mon|tue|wed|thu|fri|sat|sun)\w*)\b", rest):
        return False  # plans, not facts: the AI (and the exam planner) take those
    if re.search(r"\bexams?\b", rest):
        return False
    return len(rest.split()) <= 12


def _items(text: str) -> list[str]:
    parts = re.split(r",| and | or ", text)
    return [p.strip() for p in parts if p.strip()]


def _join(items: list[str]) -> str:
    items = [str(i) for i in items if str(i).strip()]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def people_for(home) -> People:
    p = getattr(home, "people", None)
    if p is None:
        p = home.people = People(home)
    return p


def tool_spec() -> dict[str, Any]:
    return {"name": "housemates", "description": (
        "What the house knows about each person who lives here (diet, allergies, likes, habits), shared with "
        "everyone. remember a fact someone tells you about a housemate (or themselves), forget one, or list "
        "what's known. Food facts are used by the meal planner too."),
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["action", "person", "fact"],
                         "properties": {"action": {"type": "string", "enum": ["remember", "forget", "list"]},
                                        "person": {"type": "string", "description": "Their name, or empty for everyone."},
                                        "fact": {"type": "string", "description": "e.g. 'is vegetarian', "
                                                 "'is allergic to peanuts', 'hates coriander'. Empty for list."}}}}


def run_tool(home, args: dict, inst=None) -> str:
    p = people_for(home)
    if inst is not None and getattr(inst, "kid_mode", False) and args.get("action") != "list":
        raise PermissionError("kids' rooms can't change what the house knows about people")
    if args.get("action") == "list" or not args.get("person"):
        target = p.find(args.get("person") or "") if args.get("person") else None
        return json.dumps({target.name: p.facts(target)} if target else p.all())
    target = p.find(args["person"])
    if target is None:
        raise KeyError(f"nobody called {args['person']} lives here")
    if args["action"] == "remember":
        p.add(target, args.get("fact") or "", getattr(inst, "id", None))
        return f"saved for {target.name}"
    return f"forgot {p.forget(target, args.get('fact') or '')} about {target.name}"
