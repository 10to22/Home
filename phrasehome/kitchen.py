"""Kitchen and money: recipe scaling and conversions, what's open, the stove, prices, takeout, bets.

Recipe scaler   while a recipe's on: "make it for four", "double it", "halve it".
Conversions     "how many grams in a cup of flour", "350 fahrenheit in celsius", "2 tablespoons in ml",
                "8 ounces in grams" (US cups by default; conversions: {cup_ml: 250} for metric cups).
What's open     "what's open now?", "where can I get food right now?" (the AI and web search, your location).
Stove left on   the stove's temperature sensor (or a power plug) is hot when the last person leaves, or
                hot for a long time with nobody in the kitchen: everyone's told, plainly. `off` steps can
                switch it off for you (auto_off: true).
Price check     "which supermarket is cheapest for our list?", "how much is milk at Aldi?" (web search).
Takeout split   "who's in on Thai?" (phones), "I'm in, pad thai", "it came to 64 dollars": split, and it's
                on the ledger ("who owes what", "Finn paid Parker back").
Bets            "bet: Finn bets Parker a coffee it rains tomorrow", "Parker won the bet".

stove: { sensor: { node: kitchen, input: stove_temp }, hot_above: 50, max_hours: 2, off: [ { plug: oven, state: off } ] }
price_check: { stores: [Aldi, Coles, Woolworths] }
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from fractions import Fraction

from . import clock
from .life_base import Part, clean, join, money

log = logging.getLogger("phrasehome.kitchen")

GRAMS_PER_CUP = {"flour": 125, "plain flour": 125, "self raising flour": 125, "bread flour": 130, "sugar": 200,
                 "white sugar": 200, "caster sugar": 220, "brown sugar": 220, "icing sugar": 120, "powdered sugar": 120,
                 "butter": 227, "rice": 185, "oats": 90, "rolled oats": 90, "milk": 245, "water": 240, "honey": 340,
                 "cocoa": 85, "cocoa powder": 85, "cheese": 113, "grated cheese": 113, "cream": 240, "oil": 218,
                 "olive oil": 216, "yogurt": 245, "yoghurt": 245, "pasta": 100, "peanut butter": 258, "salt": 288,
                 "breadcrumbs": 110, "coconut": 85, "chocolate chips": 170, "nuts": 140, "lentils": 190}
VOLUME_ML = {"cup": 250, "cups": 250, "tablespoon": 15, "tablespoons": 15, "tbsp": 15, "teaspoon": 5,
             "teaspoons": 5, "tsp": 5, "ml": 1, "millilitres": 1, "milliliters": 1, "litre": 1000, "litres": 1000,
             "liter": 1000, "liters": 1000, "fluid ounce": 30, "fluid ounces": 30, "fl oz": 30, "pint": 473, "pints": 473}
MASS_G = {"gram": 1, "grams": 1, "g": 1, "kilo": 1000, "kilos": 1000, "kilogram": 1000, "kilograms": 1000, "kg": 1000,
          "ounce": 28.35, "ounces": 28.35, "oz": 28.35, "pound": 453.6, "pounds": 453.6, "lb": 453.6, "lbs": 453.6}
NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "twelve": 12, "half a": 0.5, "half": 0.5, "a half": 0.5, "a quarter": 0.25, "quarter": 0.25}


def _num(s: str) -> float | None:
    s = s.strip().lower()
    if s in NUM_WORDS:
        return float(NUM_WORDS[s])
    try:
        if " " in s and "/" in s:  # "1 1/2"
            a, b = s.split(" ", 1)
            return float(a) + float(Fraction(b))
        return float(Fraction(s))
    except (ValueError, ZeroDivisionError):
        return None


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 0.05:
        return str(int(round(x)))
    for frac, word in ((0.25, "a quarter"), (0.5, "a half"), (0.75, "three quarters"), (1 / 3, "a third")):
        if abs(x - int(x) - frac) < 0.04:
            return f"{int(x)} and {word}" if int(x) else word
    return f"{x:.1f}"


def convert(text: str, cup_ml: float = 240) -> str | None:
    """'how many grams in a cup of flour', '350 fahrenheit in celsius', '2 tbsp in ml'. None if it isn't one."""
    s = clean(text)
    m = re.search(r"(-?\d+(?:\.\d+)?) ?(?:degrees )?(fahrenheit|f|celsius|c)\b.*?\b(?:in|to|into) (?:degrees )?(celsius|c|fahrenheit|f)\b", s)
    if m:
        v, a, b = float(m.group(1)), m.group(2)[0], m.group(3)[0]
        if a == b:
            return None
        out = (v - 32) * 5 / 9 if a == "f" else v * 9 / 5 + 32
        return f"{_fmt(v)} {'Fahrenheit' if a == 'f' else 'Celsius'} is about {round(out)} {'Celsius' if a == 'f' else 'Fahrenheit'}."
    units = sorted(list(VOLUME_ML) + list(MASS_G), key=len, reverse=True)
    u_rx = "|".join(re.escape(u) for u in units)
    m = re.search(rf"how many ({u_rx}) (?:are )?(?:in|is) (an?|one|two|three|half a|\d+(?:\.\d+)?|\d+/\d+) ({u_rx})(?: of (.+))?$", s)
    if m:
        want, qty, have, what = m.group(1), _num(m.group(2)), m.group(3), m.group(4)
    else:
        m = re.search(rf"(?:convert )?(an?|one|two|three|half a|\d+(?:\.\d+)?|\d+/\d+|\d+ \d+/\d+) ({u_rx})(?: of (.+?))? (?:in|to|into) ({u_rx})$", s)
        if not m:
            return None
        qty, have, what, want = _num(m.group(1)), m.group(2), m.group(3), m.group(4)
    if qty is None:
        return None
    vol = dict(VOLUME_ML, cup=cup_ml, cups=cup_ml)
    if have in vol and want in vol:
        out = qty * vol[have] / vol[want]
    elif have in MASS_G and want in MASS_G:
        out = qty * MASS_G[have] / MASS_G[want]
    else:
        food = (what or "").strip()
        g_cup = GRAMS_PER_CUP.get(food) or next((v for k, v in GRAMS_PER_CUP.items() if k in food), None)
        if g_cup is None:
            return f"Depends what it is. Tell me the ingredient, like a cup of flour."
        g_ml = g_cup / 240.0
        if have in vol:
            out = qty * vol[have] * g_ml / MASS_G[want]
        else:
            out = qty * MASS_G[have] / g_ml / vol[want]
    item = f" of {what}" if what else ""
    return f"{_fmt(qty)} {have}{item} is about {_fmt(out)} {want}."


def scale_line(line: str, factor: float) -> str:
    def rep(m):
        v = _num(m.group(0))
        return _fmt(v * factor) if v is not None else m.group(0)

    return re.sub(r"\d+ \d+/\d+|\d+/\d+|\d+(?:\.\d+)?", rep, line, count=1)


class Kitchen(Part):
    section = "conversions"

    def heard(self, said, room, speaker):
        s = clean(said)
        ans = convert(s, float(self.cfg.get("cup_ml", 240)))
        if ans:
            self.say(ans, room=room)
            return "done"
        from .ai_plus import recipe_now

        r = recipe_now(self.home, room)
        if r is not None:
            m = re.match(r"^(?:make it|scale it|scale (?:the )?recipe|change it) (?:for|to) (\w+)(?: people| serves| servings)?$", s)
            factor = None
            if m:
                n = _num(m.group(1))
                base = float(r.get("servings") or 4)
                factor = n / base if n else None
                if n:
                    r["servings"] = n
            elif re.match(r"^double (?:it|the recipe)$", s):
                factor = 2.0
            elif re.match(r"^halve (?:it|the recipe)$|^half (?:it|the recipe)$", s):
                factor = 0.5
            if factor:
                r["ingredients"] = [scale_line(x, factor) for x in r["ingredients"]]
                self.say("For that many: " + "; ".join(r["ingredients"]) + "." if r["ingredients"]
                         else "Done, but I don't have the ingredient amounts.", room=room)
                return "done"
        if re.match(r"^what(?:'s| is) open (?:now|right now|near us|nearby|at this hour)|^where can i (?:get|grab) (?:food|something to eat)(?: now| right now)?$|"
                    r"^is anything open", s):
            return self._ai(room, speaker, "Search the web: which places near "
                            f"{self._where()} are open right now ({clock.now().strftime('%A %H:%M')}) for food? Name two or "
                            "three with how late they're open, in two short spoken sentences. Question: " + said)
        m = re.match(r"^(?:price check|which (?:supermarket|shop|store) is cheapest)(?: for (?:our|the|this week's) (?:shopping )?list)?(.*)$|"
                     r"^how much (?:is|are) (.+?) (?:at|from) (.+)$", s)
        if m:
            items = self.home.instances.list_get(self.home.instances.main, "shopping")
            stores = self.cfg_stores()
            ask = (f"Search the web for current prices near {self._where()}. Shopping list: {items or 'none'}. "
                   f"Stores to compare: {stores or 'the big supermarkets nearby'}. Question: {said}. Answer in two short "
                   "spoken sentences: which is cheapest and roughly by how much. Say they're estimates.")
            return self._ai(room, speaker, ask)
        return None

    def cfg_stores(self) -> str:
        return ", ".join(str(x) for x in (self.home.config.extra.get("price_check") or {}).get("stores", []))

    def _where(self) -> str:
        a = getattr(self.home, "assistant", None)
        loc = getattr(a, "location", "") if a else ""
        if not loc:
            l = self.home.config.location
            loc = f"latitude {l['latitude']}, longitude {l['longitude']}" if "latitude" in l else "here"
        return loc

    def _ai(self, room, speaker, question: str):
        a = getattr(self.home, "assistant", None)
        if a is None:
            return "needs_ai", {}
        try:
            answer = a.ask(question, room=room, person=getattr(speaker, "id", None))
        except Exception as e:  # no key, Ollama not running, offline
            log.warning("kitchen: the AI couldn't answer: %s", e)
            return "needs_ai", {}
        self.say(answer, room=room)
        return "done"


# ================================================================ stove
class Stove(Part):
    section = "stove"

    def __init__(self, home) -> None:
        super().__init__(home)
        self.hot_since: float | None = None
        self._warned = 0.0

    def hot(self) -> bool:
        s = self.cfg.get("sensor") or {}
        if s and self.home.sensors:
            cur = self.home.sensors.current(s.get("node"), s.get("input"))
            if cur and cur.get("value") is not None:
                return cur["value"] >= float(self.cfg.get("hot_above", 50))
        plug = self.cfg.get("plug")
        energy = getattr(self.home, "energy", None)
        if plug and energy:
            try:
                return float(energy.plug(plug).watts or 0) >= float(self.cfg.get("on_above_watts", 200))
            except Exception:
                return False
        return False

    def alert(self, why: str) -> None:
        if time.time() - self._warned < 1800:
            return
        self._warned = time.time()
        self.home.events.log("stove.alert", why=why)
        self.tell(self.housemates(), "stove_alert", title="Stove", why=why)
        if self.cfg.get("auto_off") and self.cfg.get("off"):
            self.home.run_steps(self.cfg["off"], {}, "stove left on")
            self.tell(self.housemates(), "stove_off", title="Stove")
        elif self.home.state.home_people():
            self.home.voice.speak("stove_alert", everywhere=True, emotion="clear", why=why)

    def on_event(self, ev):
        if not self.cfg:
            return
        if ev["kind"] == "presence.everyone_left" and self.hot():
            self.alert("nobody's home and the stove's still hot")

    def tick(self, now):
        if not self.cfg:
            return
        if self.hot():
            self.hot_since = self.hot_since or time.time()
            rooms = getattr(self.home, "rooms", None)
            kitchen = str(self.cfg.get("room", "kitchen"))
            empty = rooms is not None and rooms.is_occupied(kitchen) is False
            if time.time() - self.hot_since > float(self.cfg.get("max_hours", 2)) * 3600 and empty:
                self.alert(f"it's been on for over {int(float(self.cfg.get('max_hours', 2)))} hours with nobody in the kitchen")
        else:
            self.hot_since = None


# ================================================================ takeout + bets (the ledger lives in points.py)
class Takeout(Part):
    def __init__(self, home) -> None:
        super().__init__(home)
        self.order: dict | None = None

    def heard(self, said, room, speaker):
        s = clean(said)
        me = speaker if speaker is not None and not speaker.main else None
        pts = self.home.life.points
        m = re.match(r"^(?:who's in on|who wants|anyone want|anyone keen on|i'm ordering|i'm getting|ordering) (.+?)(?:,? who's in)?$", s)
        if m and me is not None and not re.search(r"\bcoffee\b", s):
            self.order = {"by": me.id, "food": m.group(1), "people": {me.id: ""}, "t": time.time()}
            self.tell(self.others(me), "takeout_ask", title="Takeout", name=me.name, food=m.group(1))
            return "takeout_ok", {"food": m.group(1)}
        m = re.match(r"^(?:i'm in|count me in|me too|i'm keen|yes please|i'll have (?:some|one))(?:,? (?:i'll have |get me )?(.+))?$", s)
        if m and self.order and me is not None:
            self.order["people"][me.id] = m.group(1) or ""
            owner = self.home.instances.all.get(self.order["by"])
            self.tell([owner], "takeout_joined", title="Takeout", name=me.name, item=m.group(1) or "whatever",
                      food=self.order["food"])
            return "takeout_in", {"name": me.name}
        if re.match(r"^(?:i'm out|count me out|not for me)$", s) and self.order and me is not None:
            self.order["people"].pop(me.id, None)
            return "takeout_out", {}
        m = re.match(r"^(?:it came to|the total (?:was|is)|order total|total was|that was|the bill was) \$?(\d+(?:\.\d+)?)", s)
        if m and self.order:
            total = float(m.group(1))
            people = list(self.order["people"])
            share = round(total / max(len(people), 1), 2)
            payer = self.home.instances.all.get(self.order["by"])
            for pid in people:
                if pid != payer.id:
                    pts.owe(self.home.instances.all[pid], payer, f"${share:g}", share, why=self.order["food"])
            text = f"{len(people)} ways, that's ${share:.2f} each, paid to {payer.name}".replace(".00", "")
            self.order = None
            return "takeout_split", {"split": text}
        if re.match(r"^who owes (?:what|who|money)|^what do i owe|^the ledger$|^debts$", s):
            t = pts.owes_text()
            return ("owes_list", {"items": t}) if t else ("owes_none", {})
        m = re.match(rf"^({self.who_rx()}|i) paid (?:({self.who_rx()})|you|me) back$|^settle up(?: with ({self.who_rx()}))?$", s)
        if m:
            debtor = me if (m.group(1) in (None, "i")) else self.find(m.group(1))
            creditor = self.find(m.group(2) or m.group(3)) if (m.group(2) or m.group(3)) else None
            if debtor is None:
                return None
            n = pts.paid(debtor, creditor)
            return "owes_paid", {"name": debtor.name, "n": n}
        return None


class Bets(Part):
    store = "bets"

    def heard(self, said, room, speaker):
        s = clean(said)
        who = self.who_rx()
        me = speaker if speaker is not None and not speaker.main else None
        m = re.match(rf"^(?:bet[:,]? )?({who}|i) bets? ({who}) (.+?) (?:that|it'll|it will|they'll) (.+)$", s)
        if m:
            a = me if m.group(1) == "i" else self.find(m.group(1))
            b = self.find(m.group(2))
            if a is None or b is None or a.id == b.id:
                return None
            d = self.load({"bets": []})
            d["bets"].append({"id": uuid.uuid4().hex[:6], "a": a.id, "b": b.id, "stake": m.group(3), "claim": m.group(4),
                              "open": True, "time": clock.now().strftime("%Y-%m-%d")})
            self.save(d)
            return "bet_saved", {"a": a.name, "b": b.name, "stake": m.group(3), "claim": m.group(4)}
        m = re.match(rf"^({who}|i) (won|lost) the bet(?: (?:against|with) ({who}))?$", s)
        if m:
            p = me if m.group(1) == "i" else self.find(m.group(1))
            if p is None:
                return None
            d = self.load({"bets": []})
            open_ = [x for x in d["bets"] if x["open"] and p.id in (x["a"], x["b"])]
            if m.group(3):
                other = self.find(m.group(3))
                open_ = [x for x in open_ if other and other.id in (x["a"], x["b"])]
            if not open_:
                return "bet_none", {}
            bet = open_[-1]
            bet["open"] = False
            self.save(d)
            winner_id = p.id if m.group(2) == "won" else (bet["b"] if bet["a"] == p.id else bet["a"])
            loser_id = bet["b"] if winner_id == bet["a"] else bet["a"]
            amount = money(bet["stake"]) if re.search(r"\d", bet["stake"]) else None
            self.home.life.points.owe(self.home.instances.all[loser_id], self.home.instances.all[winner_id],
                                      bet["stake"], amount, why="bet: " + bet["claim"])
            return "bet_settled", {"winner": self.name(winner_id), "loser": self.name(loser_id), "stake": bet["stake"]}
        if re.match(r"^what bets are (?:open|on)|^open bets|^any bets", s):
            open_ = [x for x in self.load({"bets": []})["bets"] if x["open"]]
            if not open_:
                return "bet_none", {}
            return "bet_list", {"items": join(f"{self.name(x['a'])} bet {self.name(x['b'])} {x['stake']} that {x['claim']}" for x in open_)}
        return None
