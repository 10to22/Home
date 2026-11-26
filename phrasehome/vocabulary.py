"""The house's vocabulary: the many ways people actually say things (used by wordbank.py).

FAMILIES   ways of saying the same kind of command. A phrase in your config that fits any form in a
           family ("turn on the {x}") also gets every other form ("flick the {x} on", "whack the {x}
           on", "get the {x} going"...). Most common first, so a small vocabulary (a Pi) keeps the best.
PHRASES    whole sentences that mean the same ("good night" = "night night", "off to bed"...)
THINGS     words for the same thing inside a phrase (telly = TV, tunes = music, aircon = AC)
PLACES     names for the same room ("lounge", "lounge room", "front room" = living room). These also
           work as values of a {room} slot, so "turn on the lounge lights" switches the living room.
Filler, question openers and request verbs are at the bottom (Australian, British and American,
and how students talk).

Add your own the same way; a wording never replaces a phrase written in config.yaml.
"""

from __future__ import annotations

# A form starting with "~" is only ever said, never matched against your phrase ("more {x}" is a fine
# way to ask for more volume, but "more coffee" in your config shouldn't turn into "turn up the coffee").
FAMILIES: dict[str, list[str]] = {
    "on": [
        "turn on the {x}", "turn the {x} on", "switch on the {x}", "{x} on", "switch the {x} on",
        "put the {x} on", "put on the {x}", "get the {x} on", "flick on the {x}", "flick the {x} on",
        "whack the {x} on", "whack on the {x}", "chuck the {x} on", "chuck on the {x}", "pop the {x} on",
        "pop on the {x}", "power on the {x}", "power up the {x}", "~can we get the {x} on",
        "~can we have the {x} on", "~i need the {x} on", "~i want the {x} on", "~on with the {x}",
    ],
    "off": [
        "turn off the {x}", "turn the {x} off", "switch off the {x}", "{x} off", "switch the {x} off",
        "shut off the {x}", "shut the {x} off", "kill the {x}", "cut the {x}", "flick off the {x}",
        "flick the {x} off", "knock off the {x}", "knock the {x} off", "power off the {x}",
        "power down the {x}", "~get the {x} off", "~can we get the {x} off", "~no more {x}",
        "~lose the {x}", "~off with the {x}",
    ],
    "up": [
        "turn up the {x}", "turn the {x} up", "raise the {x}", "increase the {x}", "{x} up",
        "crank the {x}", "crank up the {x}", "crank the {x} up", "bump up the {x}", "bump the {x} up",
        "pump up the {x}", "boost the {x}", "~more {x}", "~a bit more {x}", "bring up the {x}",
        "bring the {x} up",
    ],
    "down": [
        "turn down the {x}", "turn the {x} down", "lower the {x}", "decrease the {x}", "{x} down",
        "drop the {x}", "bring down the {x}", "bring the {x} down", "reduce the {x}", "~less {x}",
        "~a bit less {x}", "tone down the {x}", "tone the {x} down", "~ease off the {x}", "~back off the {x}",
    ],
    "dim": [
        "dim the {x}", "dim down the {x}", "make the {x} dimmer", "~turn down the {x}", "~lower the {x}",
        "soften the {x}", "~the {x} dimmer",
    ],
    "brighten": [
        "brighten the {x}", "make the {x} brighter", "~turn up the {x}", "brighten up the {x}",
        "~the {x} brighter",
    ],
    "open": [
        "open the {x}", "open up the {x}", "pull up the {x}", "~raise the {x}", "get the {x} open",
        "crack open the {x}", "pop open the {x}",
    ],
    "close": [
        "close the {x}", "shut the {x}", "pull down the {x}", "draw the {x}", "~lower the {x}",
        "close up the {x}", "get the {x} shut", "get the {x} closed",
    ],
    "lock": [
        "lock the {x}", "lock up the {x}", "secure the {x}", "bolt the {x}", "deadbolt the {x}",
        "lock down the {x}",
    ],
    "start": [
        "start the {x}", "start up the {x}", "begin the {x}", "kick off the {x}", "fire up the {x}",
        "get the {x} going", "run the {x}", "set off the {x}", "set the {x} going", "get the {x} started",
    ],
    "stop": [
        "stop the {x}", "end the {x}", "cancel the {x}", "~kill the {x}", "shut down the {x}",
        "~knock off the {x}", "~that's enough {x}", "~no more {x}", "quit the {x}",
    ],
    "play": [
        "play {x}", "~put on {x}", "start playing {x}", "~chuck on {x}", "~throw on {x}", "queue up {x}",
        "~whack on {x}", "~spin {x}", "~give me {x}", "~let's hear {x}", "~can we get {x}", "can you play {x}",
    ],
    "pause": [
        "pause {x}", "pause the {x}", "hold the {x}", "freeze the {x}", "put the {x} on hold",
    ],
    "what": [
        "what's {x}", "what is {x}",
    ],
    "check": [
        "check the {x}", "check on the {x}", "have a look at the {x}", "take a look at the {x}",
        "~how's the {x}", "~how is the {x}",
    ],
}

# whole phrases that mean the same (both ways round)
PHRASES: dict[str, list[str]] = {
    "good morning": ["morning", "mornin", "g'day", "rise and shine", "morning house", "good morning house",
                     "top of the morning", "wakey wakey"],
    "good night": ["goodnight", "night night", "nighty night", "night house", "i'm going to bed", "off to bed",
                   "time for bed", "bedtime", "bed time", "i'm heading to bed", "hitting the sack",
                   "hitting the hay"],
    "i'm home": ["i'm back", "home sweet home", "honey i'm home", "we're back", "back home", "i made it home",
                 "i'm home now", "just got home"],
    "i'm leaving": ["heading out", "i'm off", "i'm heading out", "catch you later", "see ya", "see you later",
                    "i'm out", "off i go", "gotta go", "bye house", "i'm going out", "leaving now"],
    "lockdown": ["lock everything down", "lock it all down", "full lockdown", "lock the whole house"],
    "party mode": ["party time", "let's party", "time to party", "it's party time"],
    "dinner's ready": ["dinner is ready", "food's ready", "dinner time", "come and eat", "tea's ready",
                       "grub's up", "food's up"],
    "what time is it": ["what's the time", "got the time", "what time's it", "time check", "what's the time now"],
    "what's the weather": ["how's the weather", "what's it like outside", "weather report", "what's the forecast",
                           "how's it looking outside"],
    "i'm stressed": ["i'm anxious", "i'm freaking out", "i need to calm down", "i'm overwhelmed",
                     "help me chill", "i'm so stressed"],
    "i'm cold": ["it's freezing", "it's cold in here", "i'm freezing", "it's freezing in here", "brr"],
    "i'm hot": ["it's boiling", "it's hot in here", "i'm sweating", "it's roasting", "it's way too hot"],
    "lights on": ["lights please", "let there be light", "light it up", "get some light in here"],
    "lights off": ["lights out", "kill the lights", "no lights", "darkness please"],
    "stop the music": ["kill the music", "cut the music", "music off", "enough music", "silence the music"],
    "next song": ["skip", "skip this", "skip it", "next track", "next one", "change the song"],
    "louder": ["turn it up", "crank it", "louder please", "pump it up"],
    "quieter": ["turn it down", "too loud", "quieter please", "tone it down"],
}

# words for the same thing, inside any phrase
THINGS: dict[str, list[str]] = {
    "tv": ["telly", "television", "tele"],
    "television": ["tv", "telly"],
    "telly": ["tv", "television"],
    "music": ["tunes", "some tunes", "songs", "some music"],
    "aircon": ["ac", "air con", "air conditioning", "air conditioner"],
    "ac": ["aircon", "air con", "air conditioning"],
    "air conditioning": ["aircon", "ac"],
    "vacuum": ["hoover", "robot vacuum", "roomba", "vac"],
    "dishwasher": ["dishy", "dish washer"],
    "washer": ["washing machine"],
    "washing machine": ["washer"],
    "dryer": ["tumble dryer", "drier"],
    "blinds": ["shades"],
    "shades": ["blinds"],
    "coffee": ["a coffee", "some coffee", "a brew"],
    "garage door": ["garage"],
    "heating": ["heater", "heat"],
    "heater": ["heating"],
    "temperature": ["temp"],
    "timer": ["countdown"],
    "fan": ["ceiling fan"],
    "kettle": ["jug"],
    "fridge": ["refrigerator"],
}

# names for the same room (also as {room} slot values)
PLACES: dict[str, list[str]] = {
    "living room": ["lounge", "lounge room", "front room", "family room", "sitting room", "living"],
    "bathroom": ["loo", "toilet", "washroom", "restroom", "dunny", "bath room"],
    "hallway": ["hall", "corridor", "entryway", "entry"],
    "garden": ["yard", "backyard", "back yard"],
    "porch": ["front porch", "veranda", "verandah"],
    "front door": ["front entrance", "main door"],
    "kids room": ["kid's room", "kids' room"],
    "laundry": ["laundry room"],
    "office": ["study"],
    "dining room": ["dining"],
    "kitchen": ["kitchenette"],
}

# polite bits around a command that don't change what it means
LEAD_FILLER = [
    "mate", "bro", "dude", "hey mate", "oi", "righto", "alright then", "pls", "plz", "could ya",
    "can ya", "would ya", "any chance you could", "any chance you can", "is it possible to",
    "do us a favour and", "do us a favor and", "be a legend and", "real quick", "come on",
    "would you be able to", "can you just", "could you just", "please just", "if you could", "you mind",
    "i was wondering if you could",
]
TRAIL_FILLER = [
    "mate", "bro", "dude", "cheers", "ta", "pls", "plz", "thanks house", "thank you house", "cheers house",
    "ta house", "if that's okay", "if that's ok", "if possible", "when you can", "rn", "right away",
    "straight away", "for a bit", "for now", "yeah", "eh", "thanks mate", "cheers mate", "please and thanks",
    "would ya", "could ya", "legend",
]
# openers that make it a question
ASK_STARTS = [
    "is there", "are there", "any chance", "i wanna know", "i want to know", "i was wondering",
    "do we have", "have we got", "got any", "should i", "do i need", "will it", "is it going to",
    "are we", "who's home", "who is home", "anyone home",
]
# verbs that start a request
TASK_VERBS = [
    "chuck", "whack", "flick", "crank", "bump", "kill", "cut", "kick", "pull", "bolt", "secure", "boot",
    "reboot", "restart", "lighten", "darken", "soften", "toggle", "knock", "freeze", "tone", "ease",
    "pump", "draw", "deadbolt", "dial",
]
