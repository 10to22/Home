"""What can I say? Help for the people who live here (not the config file).

    "house, what can you do"            -> the kinds of things, out loud, in a few seconds
    "what can I say about music"        -> three things to try for that topic
    http://<house>:8800/help            -> everything, by topic, with a search box (print it for the fridge)
    phrasehome cheatsheet               -> the same page as a file: cheatsheet.html

It's built from your own config.yaml, so a command you add shows up here by itself.
"""

from __future__ import annotations

import html
import re

from .life_base import Part

# topic -> (what it's called out loud, words in a command's name or wordings that put it there)
TOPICS = [
    ("lights", "lights and blinds", r"light|lamp|dim|bright|blind|curtain|glow|colou?r"),
    ("music", "music and the TV", r"music|song|play|spotify|speaker|volume|radio|tv|telly|netflix|bluetooth|movie|film"),
    ("timers", "timers, alarms and reminders", r"timer|alarm|remind|wake me|countdown|snooze"),
    ("doors", "doors and safety", r"lock|door|garage|arm|disarm|fire|panic|safe ?word|freak|smoke|security|leak"),
    ("energy", "power and appliances", r"power|energy|plug|dishwasher|washer|dryer|heating|thermostat|warmer|cooler"),
    ("house", "house life: points, jobs, parties, study, exams, meetings", r"^life_|party|study|exam|meeting|point|"
     r"shower|bins?|chore|job|owe|poll|rule|quiet|guest"),
    ("food", "food and the kitchen", r"dinner|meal|recipe|gram|cup|takeout|thai|pizza|shopping|pantry|fridge|cook|oven|coffee"),
    ("info", "the time, the weather and who's home", r"time|date|day|weather|rain|who's home|what's on|commute|traffic|"
     r"temperature|coldest|warmest|where"),
    ("modes", "house modes and privacy", r"mode|privacy|stop listening|good night|good morning|away|vacation|dnd|disturb"),
]
TOPIC_WORDS = {"light": "lights", "lamp": "lights", "blinds": "lights", "song": "music", "songs": "music",
               "tv": "music", "telly": "music", "spotify": "music", "timer": "timers", "alarm": "timers",
               "alarms": "timers", "reminder": "timers", "reminders": "timers", "door": "doors", "lock": "doors",
               "locks": "doors", "safety": "doors", "points": "house", "chores": "house", "jobs": "house",
               "party": "house", "study": "house", "studying": "house", "exams": "house", "exam": "house",
               "meetings": "house", "house life": "house", "the house": "house", "cooking": "food", "kitchen": "food",
               "dinner": "food", "weather": "info", "time": "info", "privacy": "modes", "modes": "modes"}
# things you tell the house (they're sentences, not commands, so they aren't in config.yaml)
SAY_THINGS = {
    "house": ["{other}'s a vegetarian", "I've got a calc exam on Friday at 9", "add the bins to the meeting agenda",
              "shout out to {other} for dinner", "it came to 40 dollars", "{other}'s walking home from the library",
              "set my door to busy", "who owes what", "flip a coin", "poll: pizza or tacos"],
    "food": ["how many grams in a cup of flour", "350 fahrenheit in celsius", "who's in on thai", "get me some milk"],
    "timers": ["remind me to call mum tomorrow at 5", "remind me when I get home to take the bins out",
               "wake me up at 7"],
    "info": ["what's the weather tomorrow", "where's my passport"],
}
ASK_AI = ["{name}, what should I make for dinner", "{name}, explain photosynthesis like I'm five",
          "{name}, turn the lights down and play something chill"]


NAME_TOPIC = {"hush": "modes", "fire": "doors", "whos_home": "info", "whats_on_today": "info", "help": "info",
              "arm": "doors", "disarm": "doors"}
SHORT = {"lights": "lights", "music": "music", "timers": "timers and reminders", "doors": "doors", "house": "house life",
         "food": "food", "info": "the time and weather", "modes": "modes", "energy": "power"}


def _topic_of(cmd) -> str:
    if cmd.name in NAME_TOPIC:
        return NAME_TOPIC[cmd.name]
    hay = " ".join([cmd.name.replace("_", " ")] + list(cmd.phrases)).lower()
    for key, _, rx in TOPICS:
        if re.search(rf"\b(?:{rx})", cmd.name.lower() if key == "house" and cmd.name.startswith("life_") else hay):
            return key
    return "other"


def _example(phrase: str, slots: dict) -> str:
    def one(m: re.Match) -> str:
        vals = [k for k in (slots.get(m.group(1)) or {}) if k != "a"]
        return next((v for v in ("five", "twenty", "ten") if v in vals), vals[0] if vals else m.group(1))
    return re.sub(r"\{(\w+)\}", one, phrase)


def by_topic(config) -> dict[str, list[tuple[str, list[str]]]]:
    """topic -> [(command name, [example wordings])], every command in the config."""
    out: dict[str, list[tuple[str, list[str]]]] = {}
    for cmd in config.commands:
        if not cmd.phrases:
            continue
        ex = list(dict.fromkeys(_example(p, config.slots) for p in cmd.phrases))[:3]
        out.setdefault(_topic_of(cmd), []).append((cmd.name, ex))
    return out


def _names(config, instances=None) -> tuple[str, str, str]:
    wake = str(((config.assistant or {}).get("names") or ["house"])[0]).title()
    people = [i.get("name", k) if isinstance(i, dict) else getattr(i, "name", str(i))
              for k, i in ((config.instances or {}).items() if isinstance(config.instances, dict) else [])]
    if instances is not None:
        people = [i.name for i in instances.all.values() if not i.main]
    people = [p for p in people if p] or ["Sam", "Jo"]
    return wake, people[0], people[1 % len(people)]


def page(config, instances=None) -> str:
    """The help page / cheat sheet: every command by topic, the sentences, asking the AI."""
    wake, name, other = _names(config, instances)
    topics = by_topic(config)
    labels = {k: label for k, label, _ in TOPICS} | {"other": "everything else"}
    e = html.escape

    def fill(s: str) -> str:
        return s.replace("{name}", wake).replace("{other}", other)

    sections = []
    for key in [k for k, _, _ in TOPICS] + ["other"]:
        cmds = topics.get(key) or []
        tells = SAY_THINGS.get(key, [])
        if not cmds and not tells:
            continue
        rows = "".join(f'<li><b>"{e(ex[0])}"</b>' + (f' <span>or "{"\", \"".join(e(x) for x in ex[1:])}"</span>' if ex[1:] else "")
                       + "</li>" for _, ex in sorted(cmds, key=lambda c: c[1][0]))
        rows += "".join(f'<li class="say"><b>"{e(fill(t))}"</b></li>' for t in tells)
        sections.append(f"<section><h2>{e(labels[key][0].upper() + labels[key][1:])}</h2><ul>{rows}</ul></section>")
    ai = "".join(f'<li><b>"{e(fill(q))}"</b></li>' for q in ASK_AI)
    sections.append(f"<section><h2>Anything else: ask the AI</h2><p>Start with the house's name. It knows who "
                    f"lives here, what's on, and can run any of the above.</p><ul>{ai}</ul></section>")
    return HELP_PAGE.replace("{wake}", e(wake)).replace("{sections}", "".join(sections)).replace(
        "{count}", str(sum(len(v) for v in topics.values())))


def spoken_overview(config, instances=None) -> str:
    topics = by_topic(config)
    have = [SHORT[key] for key, _, _ in TOPICS if topics.get(key) or SAY_THINGS.get(key)]
    first = next((k for k in ("music", "lights", "house") if topics.get(k)), "house")
    return (f"I do {', '.join(have[:-1])} and {have[-1]}. Ask: what can I say about {SHORT[first]}. "
            f"It's all on the dashboard's help page too.")


def spoken_topic(config, topic: str, instances=None) -> str:
    wake, name, other = _names(config, instances)
    cmds = by_topic(config).get(topic) or []
    tries = [ex[0] for _, ex in cmds][:2] + [t.replace("{other}", other) for t in SAY_THINGS.get(topic, [])][:2]
    tries = tries[:3]
    label = SHORT.get(topic, topic)
    if not tries:
        return f"I don't have anything for {label} set up yet."
    return f"For {label}, try: " + ", ".join(tries[:-1]) + (" or " if len(tries) > 1 else "") + tries[-1] + "."


class Help(Part):
    """"what can you do" / "what can I say about music"."""

    def heard(self, said, room, speaker):
        s = re.sub(r"\s+", " ", said.lower().replace("’", "'")).strip(" .!?,")
        m = re.match(r"^(?:what can (?:i|we) (?:say|ask)(?: you)?|what can you do|what do you do|how do i use you|"
                     r"what are (?:your|the) commands|what commands (?:are there|do you know)|give me some ideas)"
                     r"(?: (?:about|for|with) (?:the )?(.+))?$", s)
        if not m:
            return None
        cfg = self.home.config
        if m.group(1):
            want = m.group(1).strip()
            topic = TOPIC_WORDS.get(want) or next((k for k, label, rx in TOPICS if want in label or re.search(rx, want)), None)
            if topic is None:
                return (f"I'm not sure about {want}. Try lights, music, timers, doors, house life, food or modes.", {})
            return (spoken_topic(cfg, topic, self.home.instances), {})
        return (spoken_overview(cfg, self.home.instances), {})


HELP_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>What can I say?</title><style>
body{font:15px/1.45 -apple-system,system-ui,sans-serif;margin:0;background:#f6f6f3;color:#222}
header{padding:18px 22px;background:#222;color:#fff}header h1{margin:0;font-size:22px}header p{margin:4px 0 0;color:#ddd}
#q{margin:14px 22px;padding:9px 12px;width:min(460px,80vw);font-size:16px;border:1px solid #bbb;border-radius:8px}
main{columns:330px;column-gap:18px;padding:0 22px 30px}
section{break-inside:avoid;background:#fff;border-radius:10px;padding:10px 16px;margin:0 0 18px;box-shadow:0 1px 2px #0002}
h2{font-size:16px;margin:6px 0}ul{margin:0;padding-left:18px}li{margin:3px 0}li span{color:#666}
li.say b{color:#2b5d8a}section p{margin:4px 0 8px;color:#555}
@media print{header{background:none;color:#000;padding:0 0 6px}header p{color:#333}#q{display:none}section{box-shadow:none;border:1px solid #ccc}
main{padding:0;columns:3;font-size:11px}}
</style></head><body><header><h1>What can I say?</h1><p>No name needed for these in your own room. Elsewhere, start with
"{wake}". Blue ones are things you tell the house. {count} commands, from config.yaml.</p></header>
<input id="q" placeholder="Search: music, timer, points..." oninput="f(this.value)" autofocus>
<main>{sections}</main><script>
function f(q){q=q.toLowerCase();document.querySelectorAll('section').forEach(s=>{let any=false;
s.querySelectorAll('li').forEach(li=>{const m=!q||li.textContent.toLowerCase().includes(q)||
s.querySelector('h2').textContent.toLowerCase().includes(q);li.style.display=m?'':'none';any=any||m});
s.style.display=any?'':'none'})}
</script></body></html>"""


def main(args) -> int:
    from pathlib import Path

    from .config import load_config

    cfg = load_config(args.config)
    out = Path(args.output)
    out.write_text(page(cfg))
    print(f"Wrote {out}: open it in a browser and print it for the fridge (it's also at /help on the dashboard).")
    return 0
