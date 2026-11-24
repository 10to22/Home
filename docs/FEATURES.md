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
| Record your own | `phrasehome voice record retinal_scan_complete` |
| Built-in sounds | chime, doorbell, alarm, timer, reminder, siren (generated, nothing to download) |
| Intercom | "house, tell Sam dinner's ready", "dinner's ready" (everywhere), dashboard "Say" box |
| Time and date | "what time is it", "what's the date" |
| Echo-proof | A room module ignores its own mic while it's talking |

## AI assistant

| Feature | How |
|---|---|
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

## Dashboard and API

| Feature | How |
|---|---|
| Wall tablet / phone dashboard | `http://<pi>:8800`: doors, mode, who's home, every room's reminders and lists, activity with door photos, devices, routine buttons, Say and Ask boxes |
| Token protected | `PHRASEHOME_TOKEN` in `.env` |
| Phone shortcuts | `POST /api/ask` ("Hey Siri, ask the house..."), `/api/routine`, `/api/mode`, `/api/list`, `/api/say`, `/api/event` |
