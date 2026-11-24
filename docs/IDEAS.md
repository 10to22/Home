# Ideas: what to add next

Ease: ⭐ = config only with what's already built · ⭐⭐ = small hardware or code addition · ⭐⭐⭐ = a project

## Security
| Idea | How | Ease |
|---|---|---|
| **Fingerprint stage** after the face scan | R503 sensor on an ESP32 at the door. When the hub logs `scan.awaiting_fingerprint`, a matching finger within 30 s unlocks | ⭐⭐ |
| Door/window open sensors | Reed switches on device nodes. "The back door has been open for 10 minutes" | ⭐⭐ |
| Auto-relock | If a door stays unlocked for N minutes, lock it and say so | ⭐⭐ |
| Doorbell button | Button on the door camera: announce "someone's at the door" in every room and send the snapshot to your phone | ⭐⭐ |
| Guest passes | Approve a visitor for a set time, e.g. `phrasehome faces allow visitor-3 --for 2d` (dog sitter, cleaner) | ⭐⭐ |
| Duress phrase | A normal-sounding phrase ("house, is it going to rain?") that also silently alerts your phone | ⭐ |
| Vacation mode | Lights turn on and off at random in the evenings, plus a daily status report | ⭐⭐ |
| Liveness check | An IR camera (phone screens show up black in IR), blink detection, or "turn your head left" | ⭐⭐⭐ |
| Package watch | A porch camera notices a package left or taken | ⭐⭐⭐ |
| Leak, smoke and CO alerts | Water sensors under sinks; smoke alarm sound detection; everything flashes and the phone gets an alert | ⭐⭐ |

## Cleaning
| Idea | How | Ease |
|---|---|---|
| **Robot vacuum by voice** ("start the vacuum", "vacuum the kitchen") | Already in the example config, via Google Home (Roborock, iRobot, Ecovacs, Dreame...) | ⭐ |
| Vacuum when everyone leaves | Already part of the `leaving` routine | ⭐ |
| "Vacuum after dinner" | The AI assistant can run it; add a schedule | ⭐ |
| Self-emptying reminders | Ask the vacuum's status through Home Assistant and announce when the bin is full | ⭐⭐ |

## Comfort
| Idea | How | Ease |
|---|---|---|
| Lights follow you | mmWave presence sensors (LD2410, ~$4) on the mic satellites; lights turn off in empty rooms | ⭐⭐ |
| Circadian lighting | Warm/cool LED strips that shift color temperature through the day | ⭐⭐ |
| Sunrise alarm | Bedroom lamp fades up over 20 minutes, then "good morning" plays | ⭐ |
| Motorized blinds | ESP32 + stepper or curtain motor as a device-node channel | ⭐⭐⭐ |
| Climate per room | BME280/SHT40 temperature sensors on nodes; "house, which room is coldest?" | ⭐⭐ |
| Air quality | SCD40 CO₂ sensor: "CO₂ is high in the bedroom, open a window" | ⭐⭐ |

## AI assistant
| Idea | How | Ease |
|---|---|---|
| **Morning briefing** | "good morning" → weather, calendar, what happened overnight (door scans) | ⭐ |
| **"What happened today?"** | Already works: it reads the event log | ⭐ |
| Intercom | "house, tell the kitchen dinner's ready" → spoken in the kitchen | ⭐⭐ |
| Shopping and to-do lists | "house, add milk to the list" → a file or Google Keep / Todoist | ⭐⭐ |
| Reminders and timers | "house, remind me at 6 to take the chicken out" | ⭐⭐ |
| Knows who's talking | Speaker identification per family member, so personal reminders and kids' limits work | ⭐⭐⭐ |
| Proactive watch | The AI checks events every few minutes and speaks up only when something's off ("the garage is still open") | ⭐⭐ |
| Remembers preferences | A small memory file: "I like it at 68 at night" | ⭐⭐ |
| Homework helper / recipe walkthrough | Already works through questions; add a "step by step" mode | ⭐ |

## Energy
| Idea | How | Ease |
|---|---|---|
| Power-metering smart plugs | "house, what's using the most power?" | ⭐⭐ |
| Standby killer | "goodnight" cuts power to TV and console plugs | ⭐ |
| Solar / battery status | Read the inverter's API: "house, how much solar today?" | ⭐⭐ |

## Garden, pets and fun
| Idea | How | Ease |
|---|---|---|
| Plant watering | Soil-moisture sensor + 12 V pump/valve on a device node; skips when rain is forecast | ⭐⭐ |
| Pet feeder | Servo/stepper feeder: "house, feed the cat" | ⭐⭐ |
| Pet door | RFID collar tag + solenoid latch | ⭐⭐⭐ |
| Party lights | WLED firmware on ESP32 LED strips, synced to the speaker group | ⭐⭐ |
| Holiday modes | Halloween sounds at the door when the scanner sees a visitor | ⭐ |

## Infrastructure
| Idea | How | Ease |
|---|---|---|
| Home Assistant alongside | Use HA as the device hub for hundreds of commercial devices; phrasehome stays the voice, door and AI layer | ⭐⭐ |
| OTA firmware updates | Update ESP32s over Wi-Fi instead of USB | ⭐⭐ |
| Backups | Nightly copy of `data/` (face bank, events) to a USB stick or NAS, encrypted | ⭐ |
| Dashboard | A wall tablet page: doors, who's home, recent scans, speaker groups | ⭐⭐⭐ |
| Privacy switch | "privacy mode" pauses all mics for an hour; a physical mute switch on each satellite | ⭐⭐ |
