# Everything phrasehome can do

Every feature below is built and in `config.smarthome.example.yaml`. "Say" lines work offline and
instantly; lines starting "house, ..." go to the AI assistant.

## Rooms with their own assistant ("instances")

| Feature | How |
|---|---|
| **A personal assistant per room or person** | `instances:`. Each has its own reminders, alarms, timers, lists, notes, wake name, voice, personality and quiet hours |
| **The living room sees everyone's** | The main instance can read and manage every instance ("house, remind Sam to feed the cat at 6", "house, what are everyone's reminders?") |
| **Private personal rooms** | In Sam's room, the AI only sees Sam's things (plus shared lists). Asking about someone else's gets a polite no |
| **Shared household lists** | `shared_lists: [shopping, ...]`: anyone adds from any room, and everyone sees one list |
| **Own wake word per room** | `wake_names: [jarvis]` works in that person's room; "house" works everywhere |
| **House or home** | "house", "home", "hey house", "hey home" all wake the AI; without a name only commands run |
| **Ignores remarks** | "house party was fun" gets no answer: only tasks and questions do (word bank first, then a quick check by the fast model). Ignored ones appear on the History page |
| **Commands first** | "home, lock the front door" runs the command straight away, no AI; "lock the front door and turn off the lights" runs both |
| **Name at the end** | "what time is it, house?", "lights off, home" |
| **Stop listening** | "house, stop listening for 20 minutes": the AI stays quiet in that room; commands still work |
| **Learns from mistakes** | "I wasn't talking to you" teaches it; ignored-then-repeated teaches it the other way |
| **Speaker rules** | `no_name_needed: [sam]` in Sam's own rooms; `strangers_need_name` for guests and the TV |
| **"No, I meant the bedroom"** | Re-runs the last command with the right room (or whatever slot), and turns the wrong one back off when there's an opposite command |
| **"Say that again"** | Repeats the last answer instantly, no AI call |
| **One answer per question** | Two mics hearing the same question get one answer, not two |
| **Wake word engine** | Optional openWakeWord with your own "hey home" model, for across-the-room hearing |
| **Word bank** | Each phrase also matches the everyday ways of saying it (vocabulary.py): "flick the lights on", "whack the telly on", "chuck the kettle on", "crank the volume", "kill the music", "get the dishwasher going", "night night", "off to bed"; Australian, British, American and student slang. Rooms by their other names ("lounge", "front room" = living room, "loo", "dunny" = bathroom, "hall", "yard"). Filler around it is fine: "oi mate, could you flick the lounge lights on, cheers". 12 wordings per phrase with a fixed vocabulary (the Pi), 40 without. Everyday verbs ("hold on", "calm down") never turn into commands |
| **Own voice per room** | `voice: {piper_model, speed, volume}` per instance |
| **Own personality per room** | `persona:` per instance |
| **Kid mode** | `kid_mode: true`: age-appropriate answers, no doors, routines or house modes |
| **Quiet hours** | `quiet_hours: "22:30-07:00"`: broadcasts skip the room and replies play quieter (alarms still ring) |
| **Do not disturb** | Say "do not disturb" / "turn off do not disturb" |
| **Answers come from the room you asked in** | Room modules (mic + speaker) speak replies, reminders and timers in their own room |

## Reminders, alarms, timers, lists, memory

| Feature | How |
|---|---|
| Timers (offline) | Say "set a timer for twenty minutes", "how much time is left", "cancel the timer" |
| Named timers | "house, set a pasta timer for 9 minutes" |
| Alarms that keep ringing | "house, wake me up at 6:30 on weekdays". It rings every 30 s until you say "stop the alarm" / "I'm up" |
| Snooze | Say "snooze" (9 minutes) |
| Reminders, one-off or repeating | "house, remind me every Tuesday at 7 to take out the trash" |
| Reminders for someone else | From the living room: "house, remind Mia at 4 to practice piano" (it plays in Mia's room) |
| Reminders on your phone too | `phone:` ntfy topic per instance |
| Do something later | "house, turn off the lights in 20 minutes" |
| Lists | "house, add milk and eggs to the shopping list", "what's on my to-do list?", "clear the packing list" |
| Memory | "house, remember that the spare key is in the blue pot" / "where's the spare key?" / "forget the key thing" |
| See it all | Dashboard, or `phrasehome agenda` |

## Doors and security

| Feature | How |
|---|---|
| **Face scan at the door** | Allowed: "retinal scan complete" + fingerprint prompt. Denied or unknown: "retinal scan failed" + lock |
| **Fingerprint second factor** | The same person's finger within 30 s of their face scan unlocks (`fingerprint:` + firmware/door_fingerprint) |
| **Face-only auto-unlock** | `phrasehome faces auto-unlock Finn` (or `door_scanner: {auto_unlock: [Finn]}`): Finn's face unlocks a locked door by itself, no fingerprint, in any mode. Everyone else still needs a fingerprint. An unlocked door is left alone. Retries if the lock doesn't respond |
| Fingerprint lockout | 3 wrong fingers: lock + phone alert |
| Unknown visitors remembered | Saved as visitor-N with a photo. `phrasehome faces allow visitor-3 --name Grandma` |
| **Guest passes** | `phrasehome faces allow visitor-3 --hours 48` (dog sitter, cleaner) |
| Banned faces | `phrasehome faces deny "Name"` |
| **Auto-relock** | `auto_lock_minutes: 3`; waits until the contact sensor says the door is shut |
| Door left open alert | Automation: contact open `for: 600` seconds, then announce + phone |
| **Doorbell** | Button on the door camera: chime in every room, announcement, and the snapshot to your phone |
| Lock by voice | Say "lock the front door", "lock up", "lockdown" (unlocking by voice is deliberately off) |
| 11 pm lock check | Schedule: if the front door is unlocked at 23:00, lock it and say so |
| **Away / armed mode** | "away mode": motion inside then means lights on, warning announced, phone alert |
| Panic | Say "panic mode": every light on, doors locked, siren sound, priority phone alert. "all clear" ends it |
| **Vacation mode** | Lights turn on and off at random from sunset to 23:30 so the house looks lived-in |
| Phone alerts with photos | `type: ntfy` backend (free app) with `attach: "{photo}"` |
| Scan history | Dashboard (with thumbnails) or `phrasehome faces scans` |

## Who's home

| Feature | How |
|---|---|
| From the door scanner | An allowed face or fingerprint marks that person home |
| By voice | "I'm leaving" / "I'm home" from your room |
| By phone (optional) | `phone_ip:` per instance; the hub pings it |
| Everyone left: lock up | Automation `presence.everyone_left` → away mode + lockdown |
| First one home: welcome | Automation `presence.first_home` → lights + "welcome home" |
| Ask | Say "who's home" |

## Sensors (on the ESP32 device nodes)

| Sensor | Example |
|---|---|
| Door/window contacts (reed switch) | "The front door has been open for 10 minutes" |
| Motion (PIR / mmWave) | Night hallway light at 10% for 2 minutes; intruder alert when away |
| Water leak | Siren + announcement in every room + priority phone alert |
| Soil moisture | Water the plants automatically when dry (once every 6 hours at most) |
| Buttons | Any button can trigger anything (`state: pressed`), and presses are never missed |
| Light level / any analog | `below:` / `above:` thresholds fire once as the value crosses them |
| Offline modules | Phone alert when a device node or mic stops responding |

## Lights, devices and home

| Feature | How |
|---|---|
| Lights here / by room / all | "lights on", "turn off the kitchen lights", "all lights off" |
| Dimming | "dim the lights to forty percent" |
| Timed switching | `for: 15` on any channel: pumps, feeders, garage-door button, door strike |
| Garage door | "open the garage": opener relay pulse, and the reed switch says which way it went |
| Water the plants | Say "water the plants" (pump for 15 s) |
| Pet feeder | Say "feed the cat" |
| Porch light at sunset / off at sunrise | `schedules:` with `at: sunset` / `at: sunrise` (+ `offset`) |
| Google Home devices | Vacuum ("vacuum the kitchen"), TV, thermostat ("I'm cold"), "find my phone" |
| Robot vacuum when you leave | Part of the `leaving` routine, if nobody's left at home |

## Routines, schedules, automations

| Feature | How |
|---|---|
| Routines | lockdown, goodnight, leaving, welcome home, good morning, movie time, party, panic, all clear |
| Conditional steps | `{if: {sun: down}, then: [...], else: [...]}` |
| Conditions | `mode`, `time: "22:00-06:00"`, `days`, `sun: down`, `door: {front: locked}`, `nobody_home`, `someone_home`, `home: [sam]`, `sensor: {...}`, `any: [...]` |
| Schedules | `at: "07:15"` / `sunset` / `sunrise`, `days: weekdays`, `if:` |
| Automations | `when` (any event) + `if` + `for` (held for N seconds) + `cooldown` + `do` |
| Events from anywhere | `POST /api/event {"kind": "package"}` → `when: {event: external.package}` (phone shortcuts, Home Assistant, IFTTT) |
| Modes | home, away, night, vacation, guest: by voice, dashboard or automation |

## Voice and sound

| Feature | How |
|---|---|
| Custom wording | `voice.phrases`; lists pick one at random |
| Mean by default | The house is satirical and mean out of the box ("Somebody left the front door unlocked again. I won't say who. It was you."), and so is the AI (`persona: mean`). `style: aussie` / `persona: aussie` is the same with Australian slang (made for the Mac's "Lee" voice), `aussie_uncut` swears too (clean again in guest mode), `style: deadpan` is drier, `style: plain` / `persona: butler` are polite. Smoke, CO, leak and weather warnings always stay plain |
| Find a voice like a recording | `phrasehome voice match clip.mp3`: compares every voice on the computer (Mac voices, Piper, espeak), with and without effects, on pitch, flatness, brightness and pace, and prints the closest with its config lines. Picks among voices you have; doesn't clone |
| Machine voice | `voice: {effect: synthetic}` (also robot, deep, radio, or any ffmpeg filter) on top of any engine |
| **Australian accent** | `voice: {accent: en_AU}` picks an Aussie voice where the engine has one: the Mac's Lee or Karen (`say`), MeloTTS's `EN-AU` speaker (`engine: melo`, runs on a CPU), or Chatterbox speaking like a sample you recorded. Piper has no Australian voice yet, so it falls back to a British one and says so. Also en_GB, en_US, en_IN |
| **Moods per line** | On by default (`voice: {emotion: false}` turns them off): every line gets a delivery, far enough apart to hear: cheeky, annoyed, cheerful, calm, urgent, soft (quiet hours) or neutral. Picked from the phrase (`wake_nag` is annoyed, `welcome_home` cheerful), a `[mood]` tag the AI puts before each sentence whose feeling changes (and the words, when it forgets), `{text: ..., emotion: ...}` in your own phrases, or the words. Each engine gets it in its own terms (Piper's length/noise scales, eSpeak pitch, Mac rate and pauses, Chatterbox exaggeration, instructions for a voice server), plus pitch and loudness through ffmpeg |
| **Safety stays clear** | With moods on, smoke, CO, leak, weather, panic and alarm lines are always slow, steady and a bit louder, and skip the voice effect. Nothing overrides that |
| **Voice modeler** | Dashboard `/voice`: sliders for each mood (speed, pitch, pauses, liveliness, loudness, acting), accent and moods switches, "Play here" on your phone or "Say it in the house". CLI: `phrasehome voice tune cheeky --pitch 2`, `voice emotions` (one line in every mood), `voice say --emotion annoyed "..."` |
| **Borrow a laptop's voice** | `phrasehome voice serve` on a Mac or PC runs the heavy voice (Chatterbox, MeloTTS); the Pi uses it with `engine: http`. Speaks the OpenAI-style speech API, so Kokoro-FastAPI or chatterbox-tts-api work too. If the laptop's asleep, the Pi says it in its own voice instead |
| **Your own voice, with consent** | Chatterbox can speak like a 10-20 s sample (`chatterbox: {sample: ..., sample_consent: true}`). Only your own voice or someone who agreed; never a celebrity. Without `sample_consent` it uses its own voice |
| Record your own | `phrasehome voice record retinal_scan_complete` |
| Built-in sounds | chime, doorbell, alarm, timer, reminder, siren (generated, nothing to download) |
| Intercom | "house, tell Sam dinner's ready", "dinner's ready" (everywhere), dashboard "Say" box |
| Time and date | "what time is it", "what's the date" |
| Echo-proof | A room module ignores its own mic while it's talking |

## Freak mode

| Feature | How |
|---|---|
| A sock on the door | Say "freak mode" in a room: every other housemate's phone gets "Freak mode is on in the bedroom. Someone's getting freaky. Stay clear." The house stops listening in that room and makes no announcements there. Optional `on_start` steps (dim the lamp) |
| Ending it | "freak mode off" (heard even with the mic paused), the End button on the dashboard, or after `minutes` (90). Everyone gets the all-clear |
| Needs | Each housemate's instance has a `phone:` ntfy topic |

## The student house (house_life.py, page: /house)

| Feature | How |
|---|---|
| What the house knows about people | "house, Parker's a vegetarian", "remember Jo's allergic to peanuts", "Alex doesn't eat pork", "Finn hates coriander", "I'm vegan" (from your own room). Shared with the whole house: every AI answer, recipe, party plan and the meal planner respect it. "what do you know about Parker?", "Parker isn't vegetarian anymore". Start people off with `about: [...]` on their instance; edit them on /house |
| Party planner | "party mode" / "start the party": your `party` routine, the Spotify playlist, the guest Wi-Fi (said out loud, "what's the wifi password?"), a quiet-time countdown at 60/30/10 minutes, then the music goes down. "party's over": tomorrow's cleanup jobs are shared out (reminders + phones), taking turns week to week. Your own "party mode" command keeps working and gets the planner too |
| Exam-aware quiet hours | Exams from calendars (anything called exam/final/midterm/test), config, or "I've got a calc exam on Friday at 9". The night before: everyone else's phone and the shared rooms get a heads-up, the house is quiet from `quiet_from` (everything softer), parties end earlier, House Radio goes music-only; on the day, good luck with their alarm |
| Study mode | "study mode" in a room: focus blocks and breaks (25/5, a long break every 4), your study lamp, no announcements in there. "skip the break" (twice and it nags you), "how long until my break", "study mode off" says how much you did |
| Door status lights | An LED by each bedroom door: studying (amber), sleeping (blue), busy (red: do not disturb or freak mode, never says which), free (green). Worked out by itself, or "set my door to busy". "is Finn busy?" |
| House meetings | "call a house meeting": a time in the next week when everyone's usually home (learned from who's been home when) and nobody's calendar, exams or quiet hours clash. Everyone's told, reminded, and at the time it reads the agenda. "add the bins rota to the meeting agenda", plus the week's talking points by itself |
| Shower timer | The bathroom humidity jumping starts it (or "shower timer"): nudges at 10 and 20 minutes, a roast at 30, the week's record ("who had the longest shower?") |
| Last one out | The last person leaves with a door unlocked: it's locked, they get a text, and everyone else is told who it was (and how many times this month) |
| Windows while away | Nobody home and a window open: everyone's phone hears about it, with rain chances, every hour; "the lounge window's still open" when someone's back |
| Winter blues lamp | Dark winter mornings: when your alarm goes off or you get up before sunrise, your bright light comes on full for half an hour |
| Pollen-aware windows | "should I open the windows?": pollen, air quality, rain and temperature, and who has hay fever. A morning warning when it's high and someone's allergic; a nudge if a window opens anyway |
| Walk-home mode | "Parker's walking home from the library", the /house page, or from anywhere: "Parker walking home 20" in the ntfy app on the house's topic. Home safe when their phone, face or voice turns up; if not, a calm, plain heads-up to everyone else, then another |
| Weekly house quiz | Sunday 7pm or "quiz time": questions from the week's events (the most used command, how often it got told to shut up, the longest shower, who left the door unlocked, who got home latest, study minutes, parcels, parties) |
| House Radio | "house radio": the playlist, and every few songs a short break with the house news (weather, who's home, exams, the meeting, records, birthdays, bin night), written fresh by the AI. It names songs, never quotes lyrics |

## House points, jobs and money

| Feature | How |
|---|---|
| House points | +1 for anything good done willingly: "I did the dishes", "Parker cleaned the bathroom", the bins when it wasn't your turn, shopping for others, letting someone in, finishing your cleaning-blitz bit. −1 for a job you were given and didn't do (your bin night, your party cleanup, any "Finn, your job is to clean the bathroom by Sunday"). Your own job done on time is just done. "that's a lie" takes a point back (another person, within 10 minutes). Three self-claimed points a day at most. "house points", "how many points do I have"; Sunday evening winner; +1/−1 buttons on /house |
| Who owes whom | Takeout ("who's in on Thai?", "I'm in, pad thai", "it came to 60 dollars": split), bets ("Finn bets Parker a coffee it rains tomorrow", "Parker won the bet"), "who owes what", "Jo paid Finn back" |

## Living together

| Feature | How |
|---|---|
| Noise meter | Quiet hours (and exam nights, and after a party's quiet time): too loud in a shared room for 20 seconds and it says so. Loudness only, never words. "how loud is it in here?" helps set `noise: {level}` |
| Bathroom queue | "I'm next for the shower"; told when it's free (door sensor, shower timer, or "bathroom's free"). A hall LED can show free/busy |
| Bin night | Whoever's turn it is gets reminded every half hour until "bins are out" |
| Borrow log | "Parker borrowed my charger", "who has my charger?", "Parker gave back my charger" |
| Rules and the lease | "new house rule: shoes off inside", "are we allowed to...?", upload the lease on /house: "what does the lease say about pets?" (not legal advice) |
| Referee | "flip a coin", "roll a dice", "pick one of us", "settle this: ..." |
| Locked out | "I'm locked out" (at the door, or "Finn locked out" on the house's ntfy topic): whoever's home is asked; nobody home, everyone's asked who's closest. Never unlocks on a message |
| Polls | "poll: pizza or tacos?" to every phone; "I vote pizza" or "vote pizza" from the phone; results when everyone's in |
| Thermostat truce | Who keeps changing it, and the fair middle: "thermostat truce" |
| House calendar | "the inspection is on Friday at 10", "Jo's got a guest staying Saturday", "what's on this week?"; book the TV or lounge ("I've got the TV Saturday at 7"); phones subscribe to /house.ics |
| Errands | "anyone going to the shops?", "I'm going to the shops", "get me milk" |
| Moving in | "I'm bringing a kettle", "who's bringing a toaster?", "what do we still need?" |
| Birthday stealth | "secret from Parker: surprise dinner Saturday": never said in Parker's room or to Parker; a week before, the others get the plans |

## Uni life

| Feature | How |
|---|---|
| Deadline crunch | Deadlines from calendars or "my essay is due Friday at 5, 2000 words"; the last 24 hours: door busy, room quiet, hourly check-ins. "I'm at 1200 words" keeps the pace |
| Study together | "join Parker", "study together in the lounge": one timer, the same breaks |
| Readings aloud | PDF, web page or text on /house (or "read my screen"): "pause reading", "keep reading" |
| Slides and photos | "explain this slide" from your phone's camera on /house, or the laptop screen |
| Exam-morning kit | An hour before: "student card, calculator, pens, water?" |
| Group projects | "group project: marketing report due Friday with Sam and Lee", "I'm doing the intro", "what's left on the marketing report?" |
| Grades | "my calc grades are assignments 85 worth 40 percent...", "what do I need on the final to get a B in calc?" |
| Hangover mode | Quiet, do not disturb, water every half hour |
| Shower slots | The night before, close-together alarms get staggered shower times |
| What to wear | Weather plus your calendar (gym, interview, formal) |

## Kitchen and safety

| Feature | How |
|---|---|
| Recipes and conversions | "double it", "make it for six" while cooking; "how many grams in a cup of flour", "350 fahrenheit in celsius" |
| What's open now / price check | Web search with your location: "what's open now?", "which supermarket is cheapest for our list?" |
| Stove left on | A temperature sensor (or power plug): hot when everyone leaves, or for hours with nobody in the kitchen: everyone's told, plainly |
| Night out | "we're going out, back by 2": anyone not back gets checked on, calmly |
| Safe word | `safe_word: {phrase}`: the house says nothing and quietly texts the others |
| Ring my phone | "where's my phone?": an urgent ntfy notification |
| New Wi-Fi devices | Reported after a day of learning what's normal; party guests ignored; approve on /house |
| Party head count | Beam counter at the door, or "five more people arrived": doors lock after the last guest leaves |

## Personal

| Feature | How |
|---|---|
| What did I miss | Since you left: deliveries, visitors, polls, jobs, points (`missed: {auto: true}` says it as you get home) |
| My calendar | "add dentist Tuesday at 3 to my calendar": CalDAV (iCloud, Fastmail, Nextcloud) or /cal/you.ics |
| Where I put things | "my passport is in the desk drawer", "where's my passport?" (keys, cards, passports only in your room) |
| Reminders by place | "remind me when I get home to call mum", "remind Parker when they get home to pay rent" |
| Nicknames | "Parks means Parker", "call me Finny" |
| My usual | "set my usual to lamp on and play lo-fi", then "my usual" |
| TV cut-off | "cut me off at 1am" (no extensions), "TV sleep timer 45 minutes": a 10-minute warning, then off |
| Mood dial | A knob on the wall picks the scene |
| Cleaning blitz / semester break | "cleaning blitz" (20 minutes, a job each); "semester break mode" (vacation, alarms paused, eco heating, back when someone's home) |
| Horoscopes / year in review | Roast horoscopes from your week (Monday 9am); "year in review" |

## Laptop companion

`phrasehome companion --person finn --hub http://<house>:8080` (add `--install` to start at login). Runs in the background: knows if you're studying, coding, in a call or distracted (decided on the laptop; only that and the app's name go to the house, never window titles, never private apps). Calls make your door say busy; distractions during study mode or a deadline crunch get a nudge; "house, look at my screen" / "what's this error?" / "explain this slide" / "read my screen" take one screenshot, only when asked. With OCR (`pip install ocrmac` on a Mac) your document's word count feeds the deadline pace, and an error stuck in your terminal gets an offer of help. "stop watching my screen" pauses it.

## Hearing, precision and learning

| Feature | How |
|---|---|
| Sounds-like matching | No word-for-word match: compares how it sounds ("tirn on the kichin lites"); never "sure", so risky commands still ask. `phonetic_match` |
| Learns from a second try | Missed, then said it differently and it worked within 20 seconds: the first wording is learned (and the misheard words, for accent fixes) |
| Learned words prime Whisper | Accent fixes and taught sentences go into Whisper's prompt; names go in as hotwords |
| Stronger Whisper | Beam search (`whisper_beam`, 5 on a Mac) and distil-medium.en on a Mac with 16 GB or more |
| Clearer moods | `voice: {emotion_strength: 1.25}` (the default): every mood a bit further from neutral; 1 is as before. Measured through the real pipeline with Piper voices: calm 0.86x speed, cheerful 1.11x and +5 semitones, annoyed 1.12x and louder, urgent 1.19x, soft 0.9x and 6 dB quieter |
| Second look at near misses | A short sentence that nearly matched a command: Vosk's next best guesses for the same audio (open vocabulary), then Whisper on a Mac if it's installed (`second_opinion`). On 300 real-voice clips: 89% -> 93% right, wrong commands halved, nothing fired on real talk or music |
| On/off safety | The word that decides on or off (up/down, open/close, lock/unlock, start/stop) heard unclearly, or changed by a second guess: "did you say ...?" instead of maybe doing the opposite (`polarity_confidence: 0.85`) |
| Train its vocabulary | `phrasehome train-hearing`: hundreds of your phrases (every command, every way of saying it) said by many voices (your Mac's voices, Piper voices including multi-speaker ones with hundreds of real readers), at different speeds, half of them over music or noise, all squeezed through mp3, then run through your recognizer. Mishearings that keep happening across voices are learned (word fixes and whole wordings, the same as teaching on the History page), never anything that would make another command fire. It also lists clashes (a phrase heard as a different command), words the Vosk model doesn't know, and accuracy before and after. `--background party.mp3` checks that real music, TV or talk runs nothing. `--dry` measures only |
| Your own recordings | `phrasehome speech-test --files ~/voice-memos`: mp3, m4a, wav... named after what was said (or `transcripts.txt` with `file|what was said`), scored like `speech-test --record` |

## Simulator scenarios

`phrasehome scenario run friday_night` (also exam_week, walk_home_late, points_week, locked_out, or `all`): a whole evening against your config in seconds, dry run, with everything the house said and sent, and PASS/FAIL checks. Write your own under `scenarios:`.

## Morning alarms that make sure you're up

| Feature | How |
|---|---|
| "Stop" from bed doesn't count | It goes quiet, then rings again after `recheck_minutes` unless the bed sensor says you got up (or, with no bed sensor, there's motion in another room) |
| Limited snoozes | `snooze_limit` per alarm; after that: "No. You've snoozed 2 times. That's not resting, that's a lifestyle." |
| No sneaking back to bed | Back in bed within `back_to_bed_minutes` and it starts again |
| Escalates | From the second nag: `escalate` steps (bedroom lights on full). After `give_up_minutes` it texts your phone |
| Rewards getting up | Says how long you took and runs `on_up` (coffee on, good morning routine) |
| Alarms page | `/alarms` on the dashboard: add, edit (time, days, room, label, all the wake settings), pause, "Ring now" to test, live status while it's going, and a log of how long everyone took |

## AI assistant

| Feature | How |
|---|---|
| Claude or a local AI | Claude by default (needs a key, pay per use). `assistant: {provider: local}` runs it on a model in Ollama instead: free, private, works offline, less reliable with house tools. `phrasehome ai-check` tests either |
| Web search for the local AI | On by default: DuckDuckGo (no setup), or your own SearXNG, or Brave (free key). It can search and read pages. Pages on your home network are off-limits, and after reading the web the AI can't touch locks, the alarm, modes or routines until that conversation ends |
| Ask anything | Say "house, ..." (web search for weather and news) |
| Morning briefing | Part of "good morning", and a weekday 7:15 schedule in Sam's room: weather, reminders, calendar, overnight door activity |
| Calendars | `calendar:` iCal link per instance: "house, what's on tomorrow?" |
| House history | "house, who came to the door today?", "when did Mia get home?" |
| Controls everything you configured | Commands, routines, doors (lock only), modes, lists, reminders, intercom |
| Follow-up | Say the name, pause; it says "Yes?" and waits for the question |

## Privacy

| Feature | How |
|---|---|
| All speech recognition is local | Audio never leaves the house; only questions you address to the AI are sent (as text) |
| Privacy mode | "stop listening" (an hour) / "start listening": nothing else is matched, logged or sent |
| Hardware mute | Mute button on each room module: no audio leaves the device; the LED stays on |
| Forget strangers | `remember_unknown: false` |
| Backups | `phrasehome backup`: one private file with config, face bank, reminders and recordings |

## Hearing and understanding

| Feature | How |
|---|---|
| Whisper | `settings: {recognizer: whisper}`: sentences are cut out of the audio, cleaned up and written down by Whisper, primed with your names, rooms and commands |
| Noise cleanup | `noise_cleanup: true`: rumble out, steady background down, volume evened out |
| The AI's name | A listener that only knows "house" catches it even when the transcript says "how's" |
| Live words | The dashboard shows words as you say them, then the sentence with unsure words underlined, and who said it |
| Did you mean | Near misses: the AI picks the command you most likely meant |
| Confirm risky things | Locks, alarm, freak mode, away, lockdown, panic, fire, leaving: "Did you say ...?" when unsure. Fire goes ahead if nobody answers |
| History + teaching | `/history`: everything heard and done, searchable; pick "It meant..." to teach a misheard sentence |
| Speech test | `phrasehome speech-test --record` then `phrasehome speech-test`: word accuracy and commands right, per recognizer |
| Speak while thinking | Answers are spoken sentence by sentence as they're written |
| Smart / fast | `assistant: {mode: fast}` or the dashboard's AI switch: qwen3:4b / Claude Haiku instead of qwen3:8b / Claude Opus |
| Who said it | Voices card: "Learn my voice", then six sentences; keeps learning when sure; says nobody when two voices are too alike |

## Testing on a laptop

| Feature | How |
|---|---|
| Voice and webcam together | `phrasehome simulate --mic --webcam`: simulated devices, your microphone and your webcam |
| Move the laptop around | The dashboard's Laptop card: which room the microphone is in, and which camera the webcam is (or off), live |

## Dashboard and API

| Feature | How |
|---|---|
| Wall tablet / phone dashboard | `http://<pi>:8800`: doors, mode, who's home, every room's reminders and lists, activity with door photos, devices, routine buttons, Say and Ask boxes |
| Token protected | `PHRASEHOME_TOKEN` in `.env` |
| Phone shortcuts | `POST /api/ask` ("Hey Siri, ask the house..."), `/api/routine`, `/api/mode`, `/api/list`, `/api/say`, `/api/event` |

---

# Round 3 additions

## Cameras and security
| Feature | How |
|---|---|
| **Security cameras around the house** | `cameras:` (ESP32-CAMs or any RTSP/IP camera), live on the dashboard |
| **Person / vehicle / animal detection** | On the Pi, no cloud: `camera` events with a snapshot, and a per-camera cooldown so one visit means one alert |
| **"What's at the door?"** | Say "who's at the door", or ask the AI about any camera; it looks at the live picture |
| **Package watch** | After someone leaves the porch, the AI checks for a package: `package.delivered` (photo to phone), `package.taken` if it vanishes and nobody from home picked it up |
| **Welcome by name** | "Retinal scan complete. Welcome, Sam." (`welcome_person` phrase too) |
| **Gentle alarm** | Arms itself in away mode after 90 s; on entry it says "the alarm is on" and gives 60 s to disarm by face, finger, voice ("disarm"), the dashboard, or someone arriving. Then a warning plus a phone alert, with no siren |
| **Night camera alerts** | A person on camera at night while away or asleep: porch light on, photo to phone |
| **Smoke and CO alarm listener** | The room mics recognize the standard smoke (T3) and CO (T4) beep patterns and trigger fire mode |
| **Fire mode** | Lights and blinds up, HVAC off so smoke isn't spread, music off, priority phone alert (unlocking for firefighters is optional) |
| **Is everything closed?** | Say "is everything closed": lists any open windows and doors and unlocked locks. Good night warns before locking up |
| **Mailbox** | A battery ESP32 in the mailbox says "you've got mail" (and warns when its battery is low) |

## Weather, traffic and info
| Feature | How |
|---|---|
| **Morning weather** | 6:45 report in the kitchen and on your phone, with air quality and pollen |
| **Severe weather mode** | Official warnings (weather.gov) are announced everywhere with the lights on; big forecast events (storms, heavy rain or snow, strong wind, heat or cold) send a heads-up |
| **Umbrella / jacket at the door** | When you open the front door in the morning: "Take an umbrella: 80 percent chance of rain" |
| **Traffic per person** | Each person's commute (work or school, or their first calendar event with a location) with live traffic |
| **Leave-now nudges** | "Sam, leave in 10 minutes for work. Traffic is heavy." Then "time to go", in whatever room they're in |
| **Car pre-heat hook** | `commute.leave_soon` automation (via Home Assistant's integration for your car) |
| **Package tracking** | Tracking numbers from any carrier (17TRACK): "out for delivery" announcements, status on the dashboard |
| **Countdowns** | "house, add a countdown to the beach trip on July 3rd", "how many days until…", dashboard card |

## Comfort
| Feature | How |
|---|---|
| **Temperature and humidity per room** | SHT40 on a device node: "what's the temperature", "which room is coldest", shown on the floor plan |
| **Sunrise wake-up** | The bedroom lamp fades up over 20 minutes before your alarm |
| **Coffee before the alarm** | `agenda.alarm_soon` turns the coffee plug on 10 minutes before a weekday alarm. Also say "make coffee" |
| **Motorized blinds** | "open the blinds", "blinds 40 percent"; open at sunrise and close at sunset; close for movies |
| **Lights follow you** | mmWave presence per room: lights on when you walk in (after dark), off when you leave |
| **Bed sensors** | When everyone's in bed, good night runs; plus a sleep log ("how did I sleep?") |
| **IR blaster** | Control old TVs, ACs and fans: learn buttons with `phrasehome ir learn tv_power --node "living room"` |
| **TV on means movie time** | The TV's power plug starts drawing power after dark: blinds close and lights dim |
| **Mood: "I'm stressed"** | Soft light, calm playlist, a one-minute guided breathing session |

## Kitchen, energy and appliances
| Feature | How |
|---|---|
| **Meal planner page** | `/meals` on the dashboard: an editable week, ingredients per meal, AI planning ("two vegetarian nights") |
| **Ingredients synced to shopping** | The week's ingredients, minus the pantry and staples, kept in the shopping list automatically; ticking an item moves it to the pantry |
| **Pantry** | "what can I make with what we have?" |
| **Allergies and dislikes** | Per person (`diet:` or "Mia is allergic to peanuts"); the planner respects them |
| **"What's for dinner?"** | Offline, from the plan |
| **Appliance done alerts** | "The washer is done" (power-monitoring plugs) |
| **Run when power is cheap** | "run the dishwasher when power is cheap" (time-of-use prices) |
| **Energy report with names** | "who used the most power?": by device and by person (plug owner, room owner, or who was in the room) |

## Music
| Feature | How |
|---|---|
| **Spotify on the house speakers** | "house, play some jazz in the kitchen", "dinner music"; or pick "House" in the Spotify app |
| **What song is this / like it** | "what song is this", "like this song" (saves it to your Liked Songs) |

## Pets, car and where things are
| Feature | How |
|---|---|
| **Pet cam** | "house, what's the dog doing?" (the AI looks at the living room camera) |
| **Pet door** | RFID collar tags open the flap only for your pets, logs who went in or out, and has locked/open modes |
| **Water bowl** | Water level sensor: "Luna's water bowl is almost empty" |
| **Feeding log** | Say "I fed the dog" / "did anyone feed the dog?" (who and when, meals today) |
| **Pet locator** | A Bluetooth collar tag: "where's Luna?" |
| **Where are my keys?** | Bluetooth tags; device nodes report which room hears them strongest |
| **Room-level presence** | Who is in which room (voice, carried tags, beds, door scans, presence sensors) on the floor plan |
| **Parking assistant** | Green, yellow, red as you pull in; tells the hub the car is home; closes the garage door after 5 minutes |

## AI
| Feature | How |
|---|---|
| **Make routines and commands by voice** | "house, every weekday at 7 turn on the coffee maker", "house, when I say 'cozy', dim the lights and play jazz" |
| **Continuous conversation** | After an answer, ask a follow-up without saying "house" |
| **Daily diary** | Written at 9:30 pm, on the dashboard; "house, what happened yesterday?" |
| **Proactive watcher** | Every hour it looks over the house and only speaks up when something's worth mentioning |
| **Welcome-back summary** | The first person home hears what happened while the house was empty |
| **Recognizes voices** | Say "this is Sam" a few times; then "remind me" in the living room goes to Sam's reminders (for personal touches, not security) |
| **Other languages** | Per room: its own speech model, phrases, voice lines and AI replies in that language |

## Dashboard
| Feature | How |
|---|---|
| Live cameras | Grid of every camera, about 2 pictures a second |
| Floor plan | Rooms with who's there, temperatures, and tags; drag and resize to match your house ("edit layout") |
| Cards | Alarm, weather, energy by person, packages (add tracking numbers), countdowns, sleep, pets, today's diary |
| Meal planner | `/meals` |
| Voice modeler | `/voice`: accent, moods, sliders per mood, preview on the phone |
