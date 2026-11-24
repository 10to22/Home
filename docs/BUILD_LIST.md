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
| **Mic satellite** (`firmware/mic_satellite`) | ESP32 DevKit, INMP441 I2S mic, USB power supply, small case | $10 |
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

### Stage 2: fingerprint (next build)
| Item | Notes | ~Cost |
|---|---|---|
| R503 capacitive fingerprint sensor (round, ring LED) | UART, stores 200 fingerprints on the sensor, has a weather-resistant metal ring. Pairs with an ESP32 at the door | $20 |

### About retinal and iris scanning
- **True retinal scanning** images the blood vessels at the back of the eye. It needs a specialized
  medical-grade optical system and the eye held a few centimeters away. It isn't something you can build from
  hobby parts.
- **Iris scanning** is more realistic but still advanced: a near-infrared camera with a macro lens
  (e.g. an OV9281 or a NoIR Pi camera with a close-focus lens), 850 nm IR lighting, and iris
  software such as Worldcoin's open-source *open-iris* pipeline. Treat it as a future research project.
- **What this project does:** face recognition with OpenCV. It is fast, contactless and works from a
  normal camera, and it keeps the "retinal scan" voice lines. Pair it with the fingerprint stage for real security.

## 4. Accounts and services

| Service | For | Cost |
|---|---|---|
| Anthropic API key (console.anthropic.com) | The AI assistant | Pay per use (a few cents for a typical question at `effort: low`; web searches cost extra) |
| Google Cloud project + Google Assistant API | Sending commands to Google Home devices | Free |
| ntfy app (ntfy.sh) | Push alerts to your phone (unknown visitor, panic) | Free |

## 5. Tools and supplies

Soldering iron + solder, jumper wires (female-female), breadboard, USB-C/micro-USB data cables,
USB-to-serial adapter, multimeter, heat-shrink tubing, double-sided foam tape, small screwdrivers,
and a 3D printer or project boxes for cases.

## 6. Rough budget for a 3-room house

| | Qty | Subtotal |
|---|---|---|
| Hub (Pi 5, SSD, UPS, speaker) | 1 | ~$190 |
| Mic satellites | 3 | ~$30 |
| Device nodes (lights) | 3 | ~$45 |
| Speakers | 3 | ~$40 |
| Door camera + IR + enclosure | 1 | ~$25 |
| Smart deadbolt | 1 | ~$200 |
| LED strips, supplies, wire | | ~$50 |
| **Total** | | **~$580** (~$400 with a DIY strike instead of a smart deadbolt) |

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
