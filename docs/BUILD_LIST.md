# Build list: everything you need

Prices are rough (USD, late 2026) and vary a lot by store. AliExpress-type sellers are cheapest
for ESP32 parts; Amazon/Adafruit/DigiKey are faster and more reliable.

## 1. The hub (one per house)

| Item | Why | ~Cost |
|---|---|---|
| **Raspberry Pi 5, 8 GB** (a Pi 4 8 GB also works, a bit slower) | Runs speech recognition for every room, face recognition, the voice, the speaker sync | $80 |
| Official 27 W USB-C power supply | Under-powered Pis crash randomly | $12 |
| NVMe SSD + M.2 HAT (or a good A2 microSD) | It runs 24/7, and SD cards wear out | $35 (SD: $12) |
| Case with fan | Face recognition keeps the CPU busy | $10 |
| **UPS HAT + 18650 cells** | Keeps locks and the scanner working through short power cuts | $30 |
| USB speaker (or a speaker module near it) | So the house can talk | $15 |
| USB mic (optional) | If the hub's own room should hear commands | $10 |
| Ethernet cable to the router | Steadier than Wi-Fi for the hub | $5 |

## 2. Per room

| Module | Parts | ~Cost |
|---|---|---|
| **Room module** (`firmware/mic_satellite`) | ESP32 DevKit, INMP441 I2S mic, MAX98357A amp + 2–3 W speaker (so the room can talk back), USB power supply, small case | $15 |
| **Device node** (`firmware/device_node`) | ESP32 DevKit, 2- or 4-channel opto-isolated relay board and/or MOSFET LED-driver board, 12 V supply + 12→5 V buck converter, case | $10–20 |
| **Speaker** (`firmware/speaker`) | *Classic* ESP32 (WROOM-32), MAX98357A amp, 3 W 4 Ω speaker, case; optional 18650 + TP4056 + boost for battery | $12 (+$8 battery) |
| LED strip (dimmable lighting) | 12 V LED strip, warm white or COB | $10–20 |

## 3. Front door

| Item | Notes | ~Cost |
|---|---|---|
| **Door camera** (`firmware/door_cam`) | AI-Thinker ESP32-CAM + ESP32-CAM-MB USB board for flashing. Mount it at face height, ~0.5–1 m from where people stand, out of direct sun | $12 |
| IR light (night scans) | 850 nm IR LED board + transistor; works with an OV2640 "night vision"/no-IR-filter lens | $6 |
| Weatherproof enclosure | IP65 box with a clear window, or a 3D-printed case with a lens cover | $8 |
| **Lock, pick one:** | | |
| • Smart deadbolt that works with Google Home or Home Assistant (Nest x Yale, Schlage Encode, Aqara U-series...) | **Recommended for exterior doors.** You keep the key and the inside thumb-turn, and it's built for weather and forced entry | $150–280 |
| • 12 V electric strike (fail-secure) + relay channel on a device node | Classic DIY: the door unlocks while powered. Needs a door-frame mortise | $25–40 |
| • 12 V solenoid bolt | For interior doors, cabinets, sheds; not strong enough for a front door | $10 |
| Door sensor (reed switch) | Tells you whether the door is actually *closed*, not just locked | $2 |
| Speaker by the door | A speaker module in group mode, so "retinal scan complete" is heard outside | $12 |

| Doorbell button | Any momentary push button (illuminated ones look great) on the door camera | $3 |

### Stage 2: fingerprint (`firmware/door_fingerprint`)
| Item | Notes | ~Cost |
|---|---|---|
| R503 capacitive fingerprint sensor (round, ring LED) | UART, stores 200 fingerprints on the sensor, weather-resistant metal ring | $20 |
| ESP32 DevKit + small weatherproof box | Runs the sensor | $6 |

## 3b. Sensors and extras (optional, per need)

| Item | For | ~Cost |
|---|---|---|
| Reed switches (magnetic door/window contacts) | Door left open, garage open, auto-relock only when shut | $1–2 each |
| PIR motion sensor (AM312 / HC-SR501) or LD2410 mmWave presence sensor | Night lights, intruder alerts while away | $2–5 |
| Water leak probe (or two screws on a 3D-printed puck) | Under sinks, water heater, washing machine | $1–3 |
| Capacitive soil moisture sensor v2 | Automatic plant watering | $2 |
| 12 V mini water pump or solenoid valve + tubing | Plant watering | $8 |
| Relay across your garage opener's wall-button terminals + reed switch | "Open the garage", "is the garage open?" | $4 |
| Gravity/auger pet feeder with a 5–12 V motor | "Feed the cat" | $15 |

### About retinal and iris scanning
- **True retinal scanning** images the blood vessels at the back of the eye. It needs a specialized
  medical-grade optical system and the eye held a few centimeters away. It isn't something you can build from
  hobby parts.
- **Iris scanning** is more realistic but still advanced: a near-infrared camera with a macro lens
  (e.g. an OV9281 or a NoIR Pi camera with a close-focus lens), 850 nm IR lighting, and iris
  software such as Worldcoin's open-source *open-iris* pipeline. Treat it as a future research project.
- **What this project does:** face recognition with OpenCV. It is fast, contactless and works from a
  normal camera, and it keeps the "retinal scan" voice lines. Pair it with the fingerprint stage for real security.

## 3c. Round 3 parts (pick what you want)

| Item | For | ~Cost |
|---|---|---|
| More ESP32-CAMs (or any RTSP IP camera) + weatherproof boxes | Perimeter cameras, pet cam | $12–35 each |
| SHT40 temperature/humidity board | Per-room climate | $4 |
| LD2410 mmWave presence sensor | Lights follow you, room presence (detects people sitting still) | $4 |
| Bed pressure mat (or load cells + HX711) | Bed sensor, sleep log | $10–15 |
| Curtain motor with dry-contact inputs, or a tubular blind motor + 2 relays | Motorized blinds | $40–120 |
| IR LED + 2N2222 transistor + TSOP38238 receiver | IR blaster and learner | $3 |
| Shelly Plug S / Plus Plug (or Tasmota plugs) | Appliance alerts, cheap-power runs, energy per person, coffee maker | $15–25 each |
| ESP32 + reed switch + 18650 + holder (mailbox) | "You've got mail" | $10 |
| HC-SR04P ultrasonic sensor + traffic-light LED module | Parking assistant | $5 |
| RDM6300 125 kHz reader + EM4100 collar tags + SG90 servo or solenoid | Pet door (fits a standard pet flap) | $15 |
| Water level sensor (or a load cell under the bowl) | Water bowl alert | $3 |
| Bluetooth tags (iTag / NUT / iBeacon keyfobs) | Where are my keys, pet locator | $2–5 each |

## 4. Accounts and services

| Service | For | Cost |
|---|---|---|
| Anthropic API key (console.anthropic.com) | The AI assistant | Pay per use (a few cents for a typical question at `effort: low`; web searches cost extra) |
| Google Cloud project + Google Assistant API | Sending commands to Google Home devices | Free |
| ntfy app (ntfy.sh) | Push alerts to your phone (unknown visitor, panic) | Free |
| Google Maps Platform key (Routes API) | Traffic and leave-now nudges | Free monthly allowance, then pay per use |
| 17TRACK API key | Package tracking | Free tier |
| Spotify Premium + a free Spotify developer app | Music by voice on the house speakers | Premium subscription |
| Open-Meteo, weather.gov | Weather, air quality, alerts | Free, no key |

## 5. Tools and supplies

Soldering iron + solder, jumper wires (female-female), breadboard, USB-C/micro-USB data cables,
USB-to-serial adapter, multimeter, heat-shrink tubing, double-sided foam tape, small screwdrivers,
and a 3D printer or project boxes for cases.

## 6. Rough budget for a 3-room house

| | Qty | Subtotal |
|---|---|---|
| Hub (Pi 5, SSD, UPS, speaker) | 1 | ~$190 |
| Room modules (mic + speaker) | 3 | ~$45 |
| Device nodes (lights) | 3 | ~$45 |
| Speakers | 3 | ~$40 |
| Door camera + IR + enclosure | 1 | ~$25 |
| Smart deadbolt | 1 | ~$200 |
| Fingerprint sensor + ESP32 | 1 | ~$26 |
| A few sensors (contacts, motion, leak) | | ~$20 |
| LED strips, supplies, wire | | ~$50 |
| **Total** | | **~$640** (~$470 with a DIY strike instead of a smart deadbolt) |

## Safety and legal checklist

- **Never make a lock that can trap people inside.** Any exterior door must open from inside without
  power, a code or a face (fire codes require this). Smart deadbolts keep a thumb-turn; electric strikes
  normally leave the inside handle working.
- **Decide what happens in a power cut.** *Fail-secure* locks stay locked; *fail-safe* locks open.
  The UPS keeps everything running through short outages. Keep a physical key.
- **Mains voltage (110/230 V):** use certified parts, enclosures and a qualified electrician, or stay on low voltage.
- **Biometrics and cameras:** in some places (for example Illinois under BIPA, and the EU/UK under GDPR)
  storing people's face data needs their consent. Set `remember_unknown: false` if you shouldn't keep
  strangers' faces. Don't aim the camera at a neighbor's property or the public sidewalk more than needed.
- **Face recognition can be fooled** by a good photo or video, and twins look alike. That's why the
  config never unlocks on a face alone by default: wait for the fingerprint stage.
