"""What the house says for the student-house features (party, study, exams, meetings, the shower
timer, last out, windows, walking home, the quiz, the radio, the winter lamp, pollen, people).

PLAIN is the polite default; MEAN, AUSSIE and UNCUT are layered on top for those styles (voice.py).
Safety lines (someone isn't home when they said they'd be, a door that wouldn't lock) are the same
plain words in every style, and always said in the clear voice.
Change any of them in config.yaml under `voice: {phrases: {party_on: "..."}}` like every other line.
"""

from __future__ import annotations

PLAIN: dict = {
    # people (people.py)
    "people_saved": "Got it: {name} {fact}.",
    "people_saved_diet": "Got it, {name} is {diet}. I'll keep that in mind for meals.",
    "people_saved_allergy": "Got it, {name} is allergic to {what}. I'll keep it out of meal plans.",
    "people_forgot": "Okay, I've forgotten that about {name}.",
    "people_nothing": "I don't know anything about {name} yet.",
    "people_about": "Here's what I know about {name}: {facts}.",
    # party (party.py)
    "party_on": "Party mode's on. {extras}",
    "party_quiet_soon": "{minutes} minutes until quiet time.",
    "party_quiet_now": "It's quiet time. Music's down. Thanks, everyone.",
    "party_off": "Party's over. {cleanup}",
    "party_cleanup": "Party cleanup time. Your job: {task}.",
    "party_wifi": "The guest Wi-Fi is {ssid}, and the password is {password}.",
    "party_quiet_when": "Quiet time is at {quiet}, in {left}.",
    "party_not_on": "There's no party on.",
    # study (study.py)
    "study_on": "Study mode on. {minutes} minutes of focus, then a break. I'll keep it quiet in here.",
    "study_break": "Break time. {minutes} minutes. Stand up, stretch, drink some water.",
    "study_long_break": "That's {blocks} blocks done. Take a proper break: {minutes} minutes.",
    "study_back": "Break's over. Back to it: {minutes} minutes.",
    "study_nag": "You've been going for {minutes} minutes and skipped your breaks. Get up for a bit.",
    "study_off": "Study mode off. {summary}",
    "study_left": "{left} left of this {phase}.",
    "study_not_on": "Study mode isn't on in here.",
    # exams
    "exam_saved": "Noted: {subject} exam {when}. The house goes quiet from {quiet} the night before. Good luck.",
    "exam_heads_up": "Heads up: {who} {has} an exam tomorrow at {time}. Quiet from {quiet} tonight, please.",
    "exam_quiet_start": "It's quiet hours now. {who} {has} an exam tomorrow.",
    "exam_good_luck": "Good luck in your exam today, {name}. You've got this.",
    "exam_none": "No exams coming up that I know of.",
    "exam_list": "Coming up: {list}.",
    "exam_removed": "Okay, I've taken that exam off.",
    # door status lights
    "door_status_set": "Your door now says {status}.",
    "door_status_clear": "Your door status is cleared.",
    "door_status_is": "{whose}: {status}.",
    # house meetings (meeting.py)
    "meeting_booked": "House meeting booked for {when}, in the {room}. Everyone's been told. To add to the "
                      "agenda, say: add something to the meeting agenda.",
    "meeting_none_free": "I couldn't find a time in the next week when everyone's home and free.",
    "meeting_soon": "House meeting in {minutes} minutes, in the {room}.",
    "meeting_now": "House meeting time. Everyone to the {room}. {agenda}",
    "meeting_agenda_added": "Added to the meeting agenda: {item}.",
    "meeting_agenda": "On the agenda: {items}.",
    "meeting_agenda_empty": "Nothing on the agenda yet.",
    "meeting_cancelled": "House meeting cancelled.",
    "meeting_notify": "House meeting {when} in the {room}. Agenda so far: {items}",
    "meeting_none": "There's no house meeting booked.",
    "meeting_when": "The house meeting is {when}, in the {room}.",
    # shower timer (housewatch.py)
    "shower_started": "Shower timer's on.",
    "shower_warn": "You've been in the shower for {minutes} minutes.",
    "shower_roast": "{minutes} minutes. That's a long shower. Others need hot water too.",
    "shower_done": "That was a {minutes} minute shower.",
    "shower_record": "This week's longest shower: {name}, {minutes} minutes.",
    "shower_none": "No showers timed this week.",
    # last out (housewatch.py)
    "last_out_locked": "You were the last one out and left the {doors} unlocked. I've locked it. That's {count} this month.",
    "last_out_shame": "{name} left the {doors} unlocked on the way out. It's locked now.",
    "last_out_couldnt": "{name} left the {doors} unlocked and I couldn't lock it. Someone please check it.",
    # windows while away
    "windows_open_away": "Nobody's home and the {windows} {is_are} open.{rain}",
    "windows_rain": " Rain is likely today, {chance} percent.",
    "windows_still_open": "Welcome back. The {windows} {is_are} still open.",
    # winter blues lamp
    "winter_lamp_on": "It's still dark out, so I've put the bright light on for {minutes} minutes. "
                      "It helps on winter mornings.",
    # pollen and fresh air
    "pollen_high": "Pollen's {level} today. Best to keep the windows shut.{who}",
    "pollen_ok": "Good time to open the windows. {why}",
    "pollen_window_opened": "Heads up: pollen's {level} today{who}. Maybe close that window.",
    "pollen_unknown": "I don't have pollen numbers for here, sorry.",
    "air_bad": "The air quality outside is {aqi}. Keep the windows shut for now.",
    # walking home (walkhome.py) - safety lines: the same in every style
    "walk_started": "Okay. I'll expect {name} home by {eta}. If not, I'll let the others know.",
    "walk_arrived": "{name} is home safe.",
    "walk_overdue": "{name} said they'd be home by {eta} and isn't yet. Maybe give them a call.",
    "walk_overdue_again": "{name} still isn't home, {minutes} minutes after they were due. Please check on them.",
    "walk_cancelled": "Okay, I've stopped waiting for {name}.",
    "walk_extended": "Okay, now expecting {name} by {eta}.",
    "walk_none": "Nobody's walking home right now.",
    # weekly quiz (quiz.py)
    "quiz_start": "It's the weekly house quiz! {count} questions, all about your week. Shout out your answers.",
    "quiz_question": "Question {n}: {question}",
    "quiz_answer": "The answer: {answer}.",
    "quiz_end": "That's the quiz. Same time next week.",
    "quiz_nothing": "Not enough happened this week for a quiz.",
    # house radio (radio.py)
    "radio_on": "You're listening to House Radio. I'll be your host.",
    "radio_off": "That's House Radio, signing off.",
}

MEAN: dict = {
    "people_saved": ["Noted: {name} {fact}. I'll remember that, unlike some people round here.",
                     "Filed under {name}: {fact}. Riveting."],
    "people_saved_diet": ["{name}'s {diet}. Noted. Their meals stay {diet}, and I'll judge everyone else's.",
                          "Got it, {name} is {diet}. I'll make sure nobody sneaks anything onto their plate."],
    "people_saved_allergy": "{name}'s allergic to {what}. Noted. No {what} in the meal plans. I'd like them alive.",
    "people_nothing": "I know nothing about {name}. Honestly, a mystery I haven't bothered to solve.",
    "people_about": "What I know about {name}: {facts}. That's the file. It's thin.",
    "party_on": ["Party mode on. Try to have fun, or at least look like you are. {extras}",
                 "Party mode. Lights, music, questionable choices. {extras}"],
    "party_quiet_soon": "{minutes} minutes until quiet time. Start wrapping up the bad decisions.",
    "party_quiet_now": ["Quiet time. The music's down and so are my expectations. Whisper or go home.",
                        "It's quiet time. If you can hear yourself, you're too loud."],
    "party_off": "Party's over. {cleanup} Yes, even you.",
    "party_cleanup": "Party cleanup. Your job: {task}. It won't do itself. I checked.",
    "party_not_on": "There's no party on. Look around. That's just your Tuesday.",
    "study_on": ["Study mode. {minutes} minutes. Phone face down. I'll know.",
                 "Study mode on. {minutes} minutes of actual effort. A bold new direction."],
    "study_break": ["Break. {minutes} minutes. Standing up counts as exercise, for you.",
                    "Break time. {minutes} minutes. Go hydrate. You look like a raisin."],
    "study_long_break": "{blocks} blocks done. Shocking, honestly. Take {minutes} minutes off.",
    "study_back": ["Break's over. The textbook missed you. Nobody else did.", "Back to it. {minutes} minutes. Go."],
    "study_nag": "{minutes} minutes, no breaks. Impressive, and slightly worrying. Stand up.",
    "study_off": "Study mode off. {summary} Academia trembles.",
    "exam_saved": "{subject} exam {when}. Noted. The house goes quiet from {quiet} the night before. "
                  "Good luck. You'll need it.",
    "exam_heads_up": "Heads up: {who} {has} an exam tomorrow at {time}. Quiet from {quiet} tonight. Yes, that means you.",
    "exam_quiet_start": "Quiet hours. {who} {has} an exam tomorrow. Be quiet, or be the reason they fail.",
    "exam_good_luck": ["Good luck today, {name}. You'll be fine. Probably. Eat something first.",
                       "Exam day, {name}. You know more than you think. Not much more, but more."],
    "door_status_set": "Your door says {status}. Whether anyone respects it is another matter.",
    "meeting_booked": "House meeting: {when}, in the {room}. Attendance is compulsory. Snacks are not provided. "
                      "Add to the agenda by saying: add something to the meeting agenda.",
    "meeting_none_free": "There's no time this week when all of you are home and free. Impressive, in a bad way.",
    "meeting_now": "House meeting. Everyone to the {room}. Yes, now. {agenda}",
    "meeting_cancelled": "House meeting cancelled. Democracy can wait.",
    "shower_warn": ["{minutes} minutes in the shower. The hot water has feelings too.",
                    "{minutes} minutes. Are you showering or composing an album in there?"],
    "shower_roast": ["{minutes} minutes. That's not a shower, it's a residency. Others need hot water before graduation.",
                     "{minutes} minutes in the shower. You've used more water than a small farm."],
    "shower_done": "Shower over: {minutes} minutes. A new personal worst.",
    "shower_record": "This week's longest shower: {name}, {minutes} minutes. A record nobody wanted.",
    "last_out_locked": "You left the {doors} unlocked on your way out, {name}. I've locked it. That's {count} this month.",
    "last_out_shame": "Public service announcement: {name} left the {doors} unlocked on the way out. "
                      "I've locked it. Shame them accordingly.",
    "windows_open_away": "Nobody's home and the {windows} {is_are} wide open. Burglars love a welcome mat.{rain}",
    "windows_still_open": "Welcome back. The {windows} {is_are} still open. Airing out the whole street, are we?",
    "winter_lamp_on": "Still dark out. Bright light's on for {minutes} minutes. Cheaper than therapy, "
                      "and I don't charge by the hour.",
    "pollen_high": "Pollen's {level} today. Windows shut, unless you enjoy sneezing.{who}",
    "pollen_window_opened": "Pollen's {level} today{who}, and you opened a window. Bold. Maybe shut it.",
    "quiz_start": "Weekly house quiz. {count} questions about your week. Some of you won't enjoy this.",
    "quiz_answer": ["The answer: {answer}. Thrilling.", "Answer: {answer}. Discuss amongst yourselves."],
    "quiz_end": "That's the quiz. Same time next week. Try to be more interesting.",
    "quiz_nothing": "Not enough happened this week for a quiz. Honestly, that says a lot.",
    "radio_on": "This is House Radio. I'm your host, and frankly overqualified.",
    "radio_off": "That's House Radio signing off. You've been a mediocre audience.",
}

AUSSIE: dict = {
    "people_saved": ["Righto: {name} {fact}. Locked in.", "Noted, mate: {name} {fact}."],
    "people_saved_diet": "Righto, {name}'s {diet}. I'll sort their tucker accordingly.",
    "people_saved_allergy": "{name}'s allergic to {what}. No {what} in the tucker, then. Noted.",
    "party_on": ["Party's on! {extras} Don't let anyone chunder in the garden.",
                 "Party mode, ya legends. {extras}"],
    "party_quiet_soon": "{minutes} minutes till quiet time, folks. Start wrapping it up.",
    "party_quiet_now": "Quiet time, everyone. Music's down. Keep it to a dull roar, yeah?",
    "party_off": "Party's done. {cleanup} No dodging it, mate.",
    "party_cleanup": "Cleanup time, mate. Your job: {task}. Get into it.",
    "study_on": "Study mode on, {minutes} minutes. Phone down, mate. I'll keep the place quiet.",
    "study_break": "Smoko. {minutes} minutes. Have a stretch and a drink of water.",
    "study_back": "Smoko's over. Back into it: {minutes} minutes.",
    "study_nag": "{minutes} minutes without a smoko, mate. Get up for a bit.",
    "study_off": "Study mode off. {summary} Good on ya.",
    "exam_heads_up": "Heads up: {who} {has} an exam tomorrow at {time}. Keep it down from {quiet} tonight, yeah?",
    "exam_good_luck": "Good luck in the exam today, {name}. You've got this, mate.",
    "meeting_booked": "House meeting's on: {when}, in the {room}. Everyone's been told. No wriggling out of it. "
                      "Add stuff by saying: add something to the meeting agenda.",
    "meeting_now": "House meeting, everyone. {room}, now. {agenda}",
    "shower_warn": "{minutes} minutes in there, mate. We're not made of hot water.",
    "shower_roast": "{minutes} minutes, ya galah. That's not a shower, that's a bloody spa retreat.",
    "shower_done": "Shower done: {minutes} minutes. Strewth.",
    "last_out_locked": "Oi {name}, you left the {doors} unlocked. Locked it for ya. That's {count} this month.",
    "last_out_shame": "Heads up: {name} left the {doors} unlocked on the way out. Locked now. Give 'em a hard time.",
    "windows_open_away": "Nobody's home and the {windows} {is_are} wide open, mate.{rain}",
    "winter_lamp_on": "Still dark out, so the bright light's on for {minutes} minutes. Bit of sunshine in a bulb.",
    "pollen_high": "Pollen's {level} today. Keep the windows shut, mate.{who}",
    "quiz_start": "It's the weekly house quiz! {count} questions about your week. Shout 'em out.",
    "radio_on": "G'day and welcome to House Radio. I'm your host.",
    "radio_off": "That's House Radio. Hooroo.",
}

UNCUT: dict = {
    "people_saved": "Righto. {name} {fact}. Filed it, mate.",
    "people_saved_diet": "{name}'s {diet}. Noted. I'll sort their bloody tucker accordingly.",
    "people_saved_allergy": "{name}'s allergic to {what}. No {what}, then. I'm not explaining a dead housemate to the landlord.",
    "people_nothing": "Don't know a bloody thing about {name} yet.",
    "party_on": ["Party's on, ya legends. {extras} Whoever chunders cleans it up. That's the rule.",
                 "Party mode. {extras} Try not to break anything this time, for fuck's sake."],
    "party_quiet_soon": "{minutes} minutes till quiet time. Start wrapping up the bloody chaos.",
    "party_quiet_now": "Quiet time. Music's down. Shut it or take it outside, and not the front yard. "
                       "The neighbours already hate us.",
    "party_off": "Party's over. {cleanup} No dodging it, ya bludgers.",
    "party_cleanup": "Cleanup, mate. Your job: {task}. It won't do itself. Get off your arse.",
    "study_on": "Study mode on. {minutes} minutes. Put the bloody phone down.",
    "study_break": "Smoko. {minutes} minutes. Stand up, ya slug. Drink some water.",
    "study_back": "Smoko's over. Back into it, {minutes} minutes. No bludging.",
    "study_nag": "{minutes} bloody minutes without a smoko. Get up before you fuse to the chair.",
    "study_off": "Study mode off. {summary} Bloody legend.",
    "exam_heads_up": "Heads up: {who} {has} an exam tomorrow at {time}. Shut up from {quiet} tonight. I mean it.",
    "exam_good_luck": "Good luck today, {name}. You'll smash it. Eat something first, ya muppet.",
    "meeting_booked": "House meeting: {when}, in the {room}. Be there or cop it. Add stuff by saying: add "
                      "something to the meeting agenda.",
    "meeting_now": "House meeting. {room}. Now. Get your arses in here. {agenda}",
    "shower_warn": "{minutes} bloody minutes. Are you washing or moving in?",
    "shower_roast": "{minutes} fucking minutes. That's not a shower, it's a bloody residency. Get out.",
    "shower_done": "Shower done: {minutes} minutes. About bloody time.",
    "shower_record": "Longest shower this week: {name}, {minutes} bloody minutes. Hot water thief.",
    "last_out_locked": "{name}, you left the bloody {doors} unlocked. Locked it. That's {count} this month, ya muppet.",
    "last_out_shame": "Oi, everyone: {name} left the {doors} unlocked on the way out. Locked it. "
                      "Give 'em shit.",
    "windows_open_away": "Nobody's home and the {windows} {is_are} wide bloody open.{rain}",
    "winter_lamp_on": "Still bloody dark. Bright light's on for {minutes} minutes. Cheaper than a shrink.",
    "pollen_high": "Pollen's {level} today. Windows shut, unless you like sneezing your bloody head off.{who}",
    "quiz_start": "Weekly house quiz, ya drongos. {count} questions about your week. Shout 'em out.",
    "quiz_end": "That's the quiz. Same time next week. Do something worth asking about.",
    "radio_on": "G'day, ya legends, this is House Radio. I'm your host, and I'm not taking requests.",
    "radio_off": "That's House Radio. Piss off, I mean, hooroo.",
}

# how each line sounds (voice_style.py); safety lines are always said clearly
MOODS: dict[str, str] = {
    **dict.fromkeys(["shower_warn", "shower_roast", "last_out_locked", "last_out_shame", "study_nag",
                     "windows_still_open"], "annoyed"),
    **dict.fromkeys(["party_on", "quiz_start", "radio_on", "exam_good_luck", "walk_arrived", "meeting_booked",
                     "study_long_break", "pollen_ok"], "cheerful"),
    **dict.fromkeys(["party_quiet_now", "study_on", "study_back", "exam_quiet_start", "winter_lamp_on",
                     "study_break", "radio_off", "people_saved_allergy"], "calm"),
    **dict.fromkeys(["party_quiet_soon", "meeting_soon", "meeting_now"], "urgent"),
    **dict.fromkeys(["quiz_answer", "quiz_question", "people_saved", "people_saved_diet", "party_cleanup",
                     "shower_record", "door_status_set"], "cheeky"),
}
SAFETY: set[str] = {"walk_overdue", "walk_overdue_again", "last_out_couldnt"}


def add_to(default_phrases: dict, styles: dict) -> None:
    """Put these lines (and house_lines2.py's) into voice.py's tables. A line already there wins."""
    for k, v in PLAIN.items():
        default_phrases.setdefault(k, v)
    layers = {"deadpan": {}, "mean": MEAN, "aussie": {**MEAN, **AUSSIE}, "aussie_uncut": {**MEAN, **AUSSIE, **UNCUT}}
    for style, lines in layers.items():
        if style in styles:
            for k, v in lines.items():
                if k not in SAFETY:
                    styles[style].setdefault(k, v)
    for style in styles.values():  # safety lines: plain words in every style
        for k in SAFETY:
            style.pop(k, None)


def _merge_second_batch() -> None:
    from . import house_lines2 as L2

    for mine, more in ((PLAIN, L2.PLAIN), (MEAN, L2.MEAN), (AUSSIE, L2.AUSSIE), (UNCUT, L2.UNCUT), (MOODS, L2.MOODS)):
        mine.update({k: v for k, v in more.items() if k not in mine})
    SAFETY.update(L2.SAFETY)


_merge_second_batch()
