# Ideas: what to add next

Most of the earlier ideas are now built. See [FEATURES.md](FEATURES.md): fingerprint unlock, door
sensors, auto-relock, doorbell, guest passes, vacation mode, leak alerts, lights following motion,
sunrise alarm, morning briefing, intercom, lists, reminders, timers, proactive alerts, preferences
memory, plant watering, pet feeder, backups, dashboard and privacy switch. What's left:

Ease: ⭐ = config only with what's already built · ⭐⭐ = small hardware or code addition · ⭐⭐⭐ = a project

## Security
| Idea | How | Ease |
|---|---|---|
| Duress phrase | A normal-sounding phrase ("house, is it going to rain?") that also silently alerts your phone | ⭐ |
| Liveness check | An IR camera (phone screens show up black in IR), blink detection, or "turn your head left" | ⭐⭐⭐ |
| Package watch | A porch camera notices a package left or taken | ⭐⭐⭐ |
| Leak, smoke and CO alerts | Water sensors under sinks; smoke alarm sound detection; everything flashes and the phone gets an alert | ⭐⭐ |

## Cleaning
| Idea | How | Ease |
|---|---|---|
| Self-emptying reminders | Ask the vacuum's status through Home Assistant and announce when the bin is full | ⭐⭐ |

## Comfort
| Idea | How | Ease |
|---|---|---|
| Circadian lighting | Warm/cool LED strips that shift color temperature through the day | ⭐⭐ |
| Sunrise alarm | Bedroom lamp fades up over 20 minutes, then "good morning" plays | ⭐ |
| Motorized blinds | ESP32 + stepper or curtain motor as a device-node channel | ⭐⭐⭐ |
| Climate per room | BME280/SHT40 temperature sensors on nodes; "house, which room is coldest?" | ⭐⭐ |
| Air quality | SCD40 CO₂ sensor: "CO₂ is high in the bedroom, open a window" | ⭐⭐ |

## AI assistant
| Idea | How | Ease |
|---|---|---|
| Knows who's talking | Speaker identification per family member, so personal reminders and kids' limits work | ⭐⭐⭐ |

## Energy
| Idea | How | Ease |
|---|---|---|
| Power-metering smart plugs | "house, what's using the most power?" | ⭐⭐ |
| Standby killer | "goodnight" cuts power to TV and console plugs | ⭐ |
| Solar / battery status | Read the inverter's API: "house, how much solar today?" | ⭐⭐ |

## Garden, pets and fun
| Idea | How | Ease |
|---|---|---|
| Pet door | RFID collar tag + solenoid latch | ⭐⭐⭐ |
| Party lights | WLED firmware on ESP32 LED strips, synced to the speaker group | ⭐⭐ |
| Holiday modes | Halloween sounds at the door when the scanner sees a visitor | ⭐ |

## Infrastructure
| Idea | How | Ease |
|---|---|---|
| Home Assistant alongside | Use HA as the device hub for hundreds of commercial devices; phrasehome stays the voice, door and AI layer | ⭐⭐ |
| OTA firmware updates | Update ESP32s over Wi-Fi instead of USB | ⭐⭐ |
