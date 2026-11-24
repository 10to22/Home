# Equipment list: everything, now and later

Everything the plan uses, in the order you'd buy it. The same list is in
[equipment.csv](equipment.csv) (with a "Bought?" column) for a spreadsheet.

**Prices** are rough US prices for late 2026 and vary a lot between stores. AliExpress-type sellers are
cheapest for ESP32 parts but take weeks; Amazon, Adafruit, SparkFun and DigiKey are faster. Buy a few
spares of the cheap parts (ESP32s, mics, amps): some arrive dead.

## The reference home these quantities are for

| | Count | Where |
|---|---|---|
| People | 3 | 2 adults + 1 kid, each with their own room assistant |
| Room modules (mic + speaker) | 5 | living room, kitchen, 2 bedrooms, entry |
| Device nodes | 9 | 5 rooms + hallway, porch, garage, garden (+ the front door lock) |
| Speaker modules | 3 + 1 | kitchen, living room, bedroom + one weatherproof by the front door |
| Doors with the scanner | 1 | front (copy the door kit for a back door) |
| Cameras | 1 + 3 + 1 | door camera, 3 outdoor, 1 indoor/pet |

**Scaling.** Each extra room adds a room module (~$30), a device node (~$25), sensors (~$15) and
optionally a speaker module (~$35): about **$70–105 per room**.

## Summary

| Phase | When | About |
|---|---|---|
| [1. Desk starter kit](#phase-1-desk-starter-kit-buy-now) | Now, to test everything | **$630** |
| [2. Core house](#phase-2-core-house-before-move-in) | Before move-in | **$1,510** (≈$1,340 with a DIY electric strike instead of a smart deadbolt) |
| [3. After move-in](#phase-3-after-move-in) | Once you know the rooms | **$750** |
| [4. Extras](#phase-4-extras-whenever-you-like) | Whenever | **$150** |
| **Everything in the plan** | | **≈ $3,040** |
| [5. Later](#phase-5-later-ideas-not-built-yet) | Ideas not built yet | ~$210 |

The biggest single items are the smart deadbolt (~$200), the outdoor cameras (~$150), the electrician
(~$250) and the blinds motors (~$180). You can start with fewer rooms and add them anytime.

## What goes into each module

| Module | Parts | ~Cost | Firmware |
|---|---|---|---|
| **Hub** | Pi 5 8 GB, 27 W supply, cooler + case, NVMe SSD + M.2 HAT, USB speaker, small UPS | ~$230 | `deploy/install.sh` |
| **Room module** | ESP32 DevKit, INMP441 mic, MAX98357A amp, 3 W speaker, USB-C supply, box | ~$30 | `mic_satellite` |
| **Device node** | ESP32 + terminal board, 2/4-ch relay, 12 V supply + buck converter, box; + whatever it drives (LED strip + MOSFET, solenoid, opener) and senses (LD2410, SHT40, reed switches, leak probe) | ~$25 + sensors | `device_node` |
| **Speaker module** | Classic ESP32, MAX98357A (or PCM5102A + amp), 5 W speaker, enclosure, supply | ~$35 | `speaker` |
| **Front door kit** | ESP32-CAM + night lens + IR board in an IP65 box, R503 + ESP32 in a box, doorbell button, door contact, outdoor speaker module, lock | ~$300 with a smart deadbolt | `door_cam`, `door_fingerprint` |
| **Outdoor camera** | RTSP/ONVIF IP camera (or ESP32-CAM in a box) | ~$30–60 | none / `door_cam` |
| **Mailbox** | Low-power ESP32 with charger, 18650, reed switch, box | ~$30 | `mailbox` |
| **Parking assistant** | ESP32, HC-SR04P, traffic-light LEDs, supply, box | ~$20 | `parking` |
| **Pet door** | Pet flap, 1–2 RDM6300 readers, EM4100 tags, servo or solenoid, ESP32, supply | ~$70 | `pet_door` |

## Things to get right when buying

- **ESP32s:** buy the classic **ESP32-WROOM-32** DevKit (38-pin). The speaker needs Bluetooth Classic,
  which the S3, C3 and C6 don't have. ESP32-WROVER boards are fine but avoid GPIO 16/17.
- **USB cables:** many cheap ones are charge-only and can't flash anything. Buy ones labeled "data".
- **Wi-Fi:** ESP32s use **2.4 GHz** only. Make sure the router broadcasts it (most do).
- **HC-SR04P**, not HC-SR04. The "P" version runs on 3.3 V; the plain one needs a voltage divider on ECHO.
- **Bluetooth tags:** iTag, NUT or iBeacon keyfobs. AirTags and Tiles change their address for privacy, so they can't be tracked this way.
- **Cameras:** check for **RTSP or ONVIF** support. Some cloud-only cameras don't offer a stream.
- **Smart plugs:** check the amp rating. US dryers are 240 V and need a Shelly EM with a clamp, installed
  by an electrician.
- **Coffee maker:** only one that starts brewing when power comes on (a mechanical switch) works with a smart plug.
- **Dishwasher on cheap power:** it must resume or start when power returns; check the manual first.
- **Locks:** every lock must open from the inside by hand. Keep a physical key.

## Running costs (monthly)

| | ~Per month | Notes |
|---|---|---|
| AI (Anthropic API) | **$15–45** | Mostly the proactive watcher (about $25 if it runs hourly; set `watcher: every_minutes: 180` for ~$8) plus questions (a few cents each), the diary (~$1) and camera checks. Rough estimates: check the usage page in the Anthropic console |
| Spotify Premium | ~$12 | Needed for voice control |
| Electricity for the system | ~$4 | Hub ~6 W + modules ~1 W each |
| Google Maps (traffic) | $0 | Normal household use stays inside the free monthly allowance |
| 17TRACK, ntfy, Open-Meteo, weather.gov, Google Assistant API | $0 | Free tiers |

## Phase 1: Desk starter kit (buy now)

One of each module, so you can test everything on a desk ([guide](GETTING_STARTED.md#stage-2-the-desk-kit-one-of-each)). All of it is reused in the house.

| ☐ | Item | Qty | ~Each | ~Total | Used for | Notes |
|---|---|---|---|---|---|---|
| ☐ | Raspberry Pi 5, 8 GB | 1 | $80 | $80 | The hub (speech, faces, cameras, dashboard) | Pi 4 8 GB also works, a bit slower |
| ☐ | Official Raspberry Pi 27 W USB-C power supply | 1 | $12 | $12 | Hub power | Under-powered Pis crash randomly |
| ☐ | Raspberry Pi 5 Active Cooler + case | 1 | $15 | $15 | Hub | Face/camera detection keeps the CPU busy |
| ☐ | microSD card 64 GB, A2 (e.g. SanDisk Extreme) | 1 | $12 | $12 | Hub storage to start | Move to an NVMe SSD in phase 2 |
| ☐ | Small USB speaker | 1 | $15 | $15 | The hub's own voice |  |
| ☐ | USB microphone (or USB conference mic) | 1 | $15 | $15 | The hub's room hears commands | Optional if every room has a room module |
| ☐ | Travel router (GL.iNet Opal/Beryl class) | 1 | $60 | $60 | A 2.4 GHz IoT network you keep when you move | Flash ESP32s once; plug it into the new internet |
| ☐ | ESP32-WROOM-32 DevKit (38-pin, USB-C), 5-pack | 1 | $30 | $30 | Room module, device node, speaker, fingerprint, spare | Must be classic ESP32 (not S3/C3) for the Bluetooth speaker |
| ☐ | INMP441 I2S microphone | 2 | $3 | $6 | Room module mic | One spare |
| ☐ | MAX98357A I2S amplifier | 2 | $5 | $10 | Room module speaker + speaker module |  |
| ☐ | Speaker 3 W 4 ohm, 40-50 mm | 2 | $4 | $8 | Room module + speaker module |  |
| ☐ | ESP32-CAM (AI-Thinker, OV2640) + ESP32-CAM-MB USB board | 1 | $12 | $12 | Door camera (and webcam-free face tests) | Often sold as a 2-pack |
| ☐ | R503 fingerprint sensor (round, ring LED) | 1 | $20 | $20 | Fingerprint stage | Library: Adafruit Fingerprint |
| ☐ | 12 V solenoid lock (small) | 1 | $10 | $10 | Stand-in for the door lock on the desk | With an IRLZ44N MOSFET + 1N4007 diode |
| ☐ | 2-channel 5 V relay module, opto-isolated | 1 | $6 | $6 | Device node outputs |  |
| ☐ | LD2410C mmWave presence sensor | 1 | $5 | $5 | Room presence, lights follow you | Sees people sitting still (PIR doesn't) |
| ☐ | SHT40 temperature/humidity breakout | 1 | $5 | $5 | Room climate | No library needed |
| ☐ | Reed switches + magnets, 5-pack | 1 | $8 | $8 | Door/window/mailbox/garage contacts |  |
| ☐ | Bluetooth tags (iTag / NUT keyfob), 2-pack | 1 | $8 | $8 | Where are my keys | Not AirTag/Tile: those change their address |
| ☐ | IR kit: 940 nm IR LEDs, TSOP38238 receivers, 2N2222 | 1 | $6 | $6 | IR blaster + learning | Library: IRremoteESP8266 |
| ☐ | Shelly Plus Plug (US) or Plug S Gen3 (EU) | 1 | $22 | $22 | Energy, appliance alerts, cheap-power runs | Or any Tasmota plug |
| ☐ | 12 V 2 A power supply + DC barrel adapters | 1 | $10 | $10 | Solenoid / LED strip tests |  |
| ☐ | Buck converter 12 V to 5 V (LM2596/MP1584), 5-pack | 1 | $8 | $8 | Power ESP32s from 12 V |  |
| ☐ | Multi-port USB charger (5 V) | 1 | $20 | $20 | Power the desk modules |  |
| ☐ | Breadboards (830-point), 3-pack | 1 | $10 | $10 | Desk wiring |  |
| ☐ | Jumper wires M-M, M-F, F-F | 1 | $8 | $8 | Desk wiring |  |
| ☐ | Resistor + LED + capacitor assortments | 1 | $20 | $20 | Pull-ups, dividers, indicator LEDs |  |
| ☐ | Transistor/MOSFET/diode kit (2N2222, IRLZ44N, 1N4007) | 1 | $10 | $10 | IR LED, solenoids, LED strips |  |
| ☐ | Push buttons + an illuminated doorbell button | 1 | $8 | $8 | Doorbell, mute, wall buttons |  |
| ☐ | USB-C and micro-USB data cables | 8 | $2 | $16 | Flashing and power | Must be data cables, not charge-only |
| ☐ | Temperature-controlled soldering iron (e.g. Pinecil) | 1 | $30 | $30 | Headers, wires |  |
| ☐ | Solder, flux, desoldering wick | 1 | $15 | $15 |  |  |
| ☐ | Wire strippers + flush cutters | 1 | $15 | $15 |  |  |
| ☐ | Multimeter | 1 | $25 | $25 | Checking power and wiring |  |
| ☐ | Helping hands, heat-shrink assortment, hot glue gun | 1 | $30 | $30 |  |  |
| ☐ | Label maker | 1 | $25 | $25 | Label every module (room, IP) |  |
| ☐ | Small screwdriver set | 1 | $15 | $15 | Terminals |  |

**Phase total: about $630**


## Phase 2: Core house (before move-in)

The rest of the modules for every room, the door kit and the lock. Build and burn them in on the desk before moving ([rehearsal](GETTING_STARTED.md#stage-3-full-rehearsal)). Quantities are what's left after phase 1.

| ☐ | Item | Qty | ~Each | ~Total | Used for | Notes |
|---|---|---|---|---|---|---|
| ☐ | M.2 HAT+ for Pi 5 + 256 GB NVMe SSD | 1 | $37 | $37 | Reliable 24/7 storage | SD cards wear out |
| ☐ | Small UPS (~400 VA) for router + Pi | 1 | $60 | $60 | Hub, locks and internet through short power cuts |  |
| ☐ | Mesh Wi-Fi (if the house is big or has floors) | 1 | $180 | $180 | 2.4 GHz coverage everywhere | Optional; check coverage on a walkthrough |
| ☐ | Ethernet cables (cat6) | 3 | $4 | $12 | Pi, cameras |  |
| ☐ | ESP32-WROOM-32 DevKit, 5-pack | 3 | $30 | $90 | The rest of the modules (about 15 boards) | Plus the P1 spares |
| ☐ | Screw-terminal breakout boards for the ESP32 | 9 | $6 | $54 | Solid wiring in device nodes | No loose jumpers in the walls |
| ☐ | INMP441 microphone | 3 | $3 | $9 | Room modules (5 total) | Living room, kitchen, 2 bedrooms, entry |
| ☐ | MAX98357A amplifier | 5 | $5 | $25 | Room modules + speaker modules |  |
| ☐ | Speaker 3 W 4 ohm | 3 | $4 | $12 | Room modules |  |
| ☐ | USB-C 5 V 1-2 A power supplies | 12 | $6 | $72 | Room modules, nodes, cameras |  |
| ☐ | Enclosures (3D-printed or project boxes) | 15 | $5 | $75 | Every module | A 3D printer (~$250) pays off at this count |
| ☐ | 2/4-channel 5 V relay modules | 6 | $7 | $42 | Device nodes (lights, lock, fan, opener) |  |
| ☐ | 12 V LED strip, warm white COB, 5 m | 4 | $20 | $80 | Dimmable room lighting |  |
| ☐ | MOSFET LED driver modules (D4184 / IRLZ44N) | 6 | $2 | $12 | Dimming LED strips |  |
| ☐ | 12 V 5 A power supplies | 4 | $15 | $60 | LED strips + nodes |  |
| ☐ | Speaker drivers 5 W (or bookshelf speakers) + enclosures | 2 | $15 | $30 | 2 more speaker modules | Or PCM5102A DAC + an amp for better sound |
| ☐ | Night-vision OV2640 lens (no IR filter, wide angle) | 1 | $5 | $5 | Door camera at night |  |
| ☐ | 850 nm IR LED illuminator board | 1 | $6 | $6 | Night face scans |  |
| ☐ | IP65 enclosure with clear window | 2 | $12 | $24 | Door camera + fingerprint |  |
| ☐ | Outdoor-rated 5 V 2 A supply | 2 | $10 | $20 | Door camera, fingerprint ESP32 |  |
| ☐ | Smart deadbolt (Google Home / Home Assistant compatible) | 1 | $200 | $200 | Front door lock | Keeps the key + inside thumb-turn. DIY alternative: 12 V fail-secure electric strike (~$35) + relay |
| ☐ | Flush door contact (reed) for the frame | 2 | $5 | $10 | Door open/closed, auto-relock |  |
| ☐ | Weatherproof 3 in speaker + MAX98357A + ESP32 | 1 | $25 | $25 | The scanner's voice outside the door | A speaker module in group mode |
| ☐ | Smoke + CO alarms (if the house lacks them) | 4 | $30 | $120 | Required anyway; the mics listen for them | Interconnected models are best |
| ☐ | Electrician visit | 1 | $250 | $250 | Strike wiring, outdoor outlets, 240 V monitor | Only for mains work |

**Phase total: about $1,510**


## Phase 3: After move-in

Things that depend on the actual rooms: outdoor cameras, sensors in every room, blinds, bed mats, plugs.

| ☐ | Item | Qty | ~Each | ~Total | Used for | Notes |
|---|---|---|---|---|---|---|
| ☐ | Outdoor IP camera with RTSP/ONVIF | 3 | $50 | $150 | Driveway, back yard, side gate | Better night vision than ESP32-CAM; check RTSP support |
| ☐ | PoE switch + outdoor Ethernet cable (if PoE cameras) | 1 | $80 | $80 | Camera power and data | Optional |
| ☐ | Indoor camera with RTSP (or ESP32-CAM) | 1 | $25 | $25 | Pet cam, indoor security |  |
| ☐ | LD2410C mmWave presence sensors | 4 | $5 | $20 | Every room |  |
| ☐ | SHT40 temperature/humidity | 4 | $5 | $20 | Every room |  |
| ☐ | Reed switches + magnets, 5-pack | 2 | $8 | $16 | Windows, garage |  |
| ☐ | Water leak probes (two contacts each) | 3 | $2 | $6 | Under sinks, water heater, washer |  |
| ☐ | Curtain motor with dry-contact inputs (or tubular blind motor) | 2 | $90 | $180 | Motorized blinds | Drive with two relays on a device node |
| ☐ | Bed occupancy pressure mat | 2 | $20 | $40 | Bed sensor, sleep log, everyone in bed |  |
| ☐ | IR LED + receiver sets | 2 | $3 | $6 | TV and AC remotes |  |
| ☐ | Shelly Plus Plugs | 5 | $22 | $110 | Washer, TV, coffee maker, PC, kettle | Check each plug's amp rating |
| ☐ | Shelly EM + 50 A clamp | 1 | $55 | $55 | 240 V dryer energy + done alert | Installed by an electrician |
| ☐ | Coffee maker with a mechanical on switch | 1 | $30 | $30 | Coffee before the alarm | Only if yours needs a button press after power-on |
| ☐ | Bluetooth tags | 4 | $3 | $12 | Keys, wallet, bag, collar |  |

**Phase total: about $750**


## Phase 4: Extras (whenever you like)

Mailbox, parking, pet door, water bowl, feeder, plants.

| ☐ | Item | Qty | ~Each | ~Total | Used for | Notes |
|---|---|---|---|---|---|---|
| ☐ | Low-power ESP32 with charger (FireBeetle 2 ESP32-E / LOLIN D32) | 1 | $10 | $10 | Mailbox sensor | Months on one charge |
| ☐ | 18650 protected cell + holder | 1 | $10 | $10 | Mailbox power |  |
| ☐ | Weatherproof box + 100k resistors | 1 | $7 | $7 | Mailbox |  |
| ☐ | HC-SR04P (3.3 V) ultrasonic sensor | 1 | $3 | $3 | Parking assistant |  |
| ☐ | Traffic-light LED module | 1 | $2 | $2 | Green/yellow/red |  |
| ☐ | Lockable pet flap (to modify) | 1 | $40 | $40 | Pet door |  |
| ☐ | RDM6300 125 kHz reader + antenna | 2 | $5 | $10 | Pet door (outside + inside for direction) |  |
| ☐ | EM4100 125 kHz collar tags, 10-pack | 1 | $6 | $6 | Pet door |  |
| ☐ | MG90S servo (or 12 V solenoid + MOSFET) | 1 | $5 | $5 | Pet door latch |  |
| ☐ | Water level sensor (XKC-Y25 non-contact or resistive) | 1 | $6 | $6 | Water bowl alert | Non-contact doesn't corrode |
| ☐ | Pet feeder you can trigger (or 12 V gear motor + 3D-printed auger) | 1 | $30 | $30 | Feed the cat by voice |  |
| ☐ | Capacitive soil moisture sensor v2 | 2 | $2 | $4 | Plant watering |  |
| ☐ | 12 V pump or solenoid valve + tubing | 1 | $20 | $20 | Plant watering |  |
| ☐ | Relay across the opener's wall-button terminals (node relay) | 1 | $0 | $0 | Open/close the garage | Uses a device node relay + reed switch |

**Phase total: about $153**


## Phase 5: Later (ideas not built yet)

Parts for ideas from the lists that aren't built yet. Tell me which you want and I'll add them.

| ☐ | Item | Qty | ~Each | ~Total | Used for | Notes |
|---|---|---|---|---|---|---|
| ☐ | Zigbee USB dongle + Zigbee sensors | 1 | $25 | $25 | Cheap commercial sensors and bulbs | Needs code |
| ☐ | WS2812 LED ring per room module | 5 | $4 | $20 | Listening/timer/alarm lights | Needs firmware |
| ☐ | ESP32 touchscreen wall panel (2.8 in CYD) | 3 | $15 | $45 | Room timers, lights, lists | Needs firmware |
| ☐ | 7.5 in e-paper display + ESP32 | 1 | $45 | $45 | Family board by the door | Needs firmware |
| ☐ | Battery buttons (ESP32-C3 + coin cell) | 3 | $6 | $18 | Bedside 'good night' button | Needs firmware |
| ☐ | Door keypad (PIN backup) or NFC reader | 1 | $8 | $8 | Backup entry | Needs code |
| ☐ | Two-way door intercom (mic + speaker at the door) | 1 | $15 | $15 | Talk to visitors from your phone | Needs code |
| ☐ | Fake-TV-light LEDs | 1 | $5 | $5 | Vacation mode |  |
| ☐ | Weather station (rain/wind) or pool/fish tank probe (DS18B20) | 1 | $20 | $20 | Garden, pool, aquarium | Needs code |
| ☐ | Water flow meter | 1 | $10 | $10 | Leak / running tap alerts | Needs code |

**Phase total: about $211**
