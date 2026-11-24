# ESP32 firmware

Two sketches, one per board:

| Sketch | What it does | Parts (approx. cost) |
|---|---|---|
| `mic_satellite/` | Mic in a room. Streams audio to the hub, blinks its LED when a command is heard | ESP32 dev board + INMP441 I2S mic (~$8) |
| `device_node/` | Switches relays and dims LED strips on command. Has its own web page with buttons | ESP32 dev board + relay module and/or MOSFET module (~$6–10) |
| `door_cam/` | Door camera for the face scanner. Serves photos to the hub | AI-Thinker ESP32-CAM + ESP32-CAM-MB flasher (~$12) |
| `speaker/` | Bluetooth speaker on its own, or one of a synced multi-room group | **Classic** ESP32 dev board + MAX98357A amp + 3 W speaker (~$10) |

You can mix and match: one mic and one device node per room, a mic-only room that controls
things elsewhere, and so on.

## Flashing (Arduino IDE)

1. Install the [Arduino IDE](https://www.arduino.cc/en/software). In **Boards Manager**, install **esp32 by Espressif**
   (version 2.x or 3.x both work).
2. Open `mic_satellite/mic_satellite.ino` or `device_node/device_node.ino`.
3. Copy `config.example.h` to `config.h` in the same folder and fill it in: Wi-Fi, room/node name, pins.
   `config.h` is git-ignored, so your Wi-Fi password stays out of git.
4. Select board **ESP32 Dev Module** (or your board), pick the port, and click **Upload**.
5. Open **Serial Monitor** at 115200 baud to see it connect.

## Wiring: mic satellite (INMP441)

```
INMP441      ESP32
  VDD  ────  3V3
  GND  ────  GND
  L/R  ────  GND        (left channel)
  SCK  ────  GPIO 26
  WS   ────  GPIO 25
  SD   ────  GPIO 33
```

Mount the mic facing the room, away from fans and speakers. To check it, run the hub with
`log_heard: true` and talk. If you see nothing, set `MIC_USE_RIGHT_CHANNEL 1`. If the hub hears
you but badly, change `GAIN_SHIFT` (lower = louder).

**LED:** one short blink means it heard a command, a long blink means it worked, and three quick
flashes mean it failed.

## Wiring: device node

**Low voltage first (recommended):** a 12 V LED strip with a MOSFET module, which is dimmable and safe to play with.

```
MOSFET module (e.g. IRLZ44N / "D4184" board)      ESP32
  SIG/PWM  ──────────────────────────────────  GPIO 22   (lamp channel, dimmable)
  GND      ──────────────────────────────────  GND
  V+ / V-  ── 12 V supply                      (also join 12 V supply GND to ESP32 GND)
  OUT+/OUT- ── LED strip
```

Power the ESP32 from the 12 V supply through a small 12 V → 5 V buck converter into `VIN`/`5V`.

**Relays** (lamps, fans, anything on/off):

```
Relay module        ESP32
  VCC  ──────────  5V / VIN
  GND  ──────────  GND
  IN1  ──────────  GPIO 21   (lights channel)
  IN2  ──────────  GPIO 23   (fan channel)
```

Optional wall button: wire a push button between `GPIO 19` and `GND` to toggle "lights" by hand.

> ⚠️ **Mains voltage (110/230 V) can kill you and start fires.** Only wire relays to mains if you're
> qualified, use a proper enclosure, and follow local electrical code. A safer route for mains lamps is a
> plug-in smart plug that Google Home already controls. Use the `google` backend for those and keep the
> ESP32s on low voltage.

Test a node from any browser: `http://phrasehome-kitchen.local` shows buttons for each channel. Add
`?token=YOURTOKEN` to the address if you set a token.

## Speaker module

**Parts:** a *classic* ESP32 (ESP32-WROOM-32 DevKit), because the S3, C3 and C6 have no Bluetooth audio.
Add a MAX98357A I2S amp board and a 4–8 Ω, 3 W speaker. For better sound, use a PCM5102A DAC board
feeding any amplifier instead (same three signal wires).

```
MAX98357A      ESP32
  VIN   ────  5V / VIN
  GND   ────  GND
  BCLK  ────  GPIO 26
  LRC   ────  GPIO 25
  DIN   ────  GPIO 22
  GAIN, SD    leave unconnected (9 dB gain, mono mix of left + right)
  + / -  ───  speaker
```

**Battery (optional):** an 18650 cell, a TP4056 charger board *with protection*, a 5 V boost converter
and a power switch. Feed the boost output to the ESP32's `5V` pin and the amp's `VIN`.

**Extra setup in the Arduino IDE (speaker only):**

1. Install the two libraries by Phil Schatzmann. On each GitHub page, use **Code → Download ZIP**, then in
   the IDE go to **Sketch → Include Library → Add .ZIP Library**:
   - <https://github.com/pschatzmann/ESP32-A2DP>
   - <https://github.com/pschatzmann/arduino-audio-tools>
2. Go to **Tools → Partition Scheme → Huge APP (3MB No OTA)**. Bluetooth and Wi-Fi together don't fit in the default partition.

**Using it:**

- **Bluetooth mode** (the default): pair your phone with **"phrasehome kitchen"** like any speaker. The LED blinks
  while it waits and stays solid when a phone is connected.
- **Group mode:** plays the Pi's music in sync with the other group speakers. The LED is solid while playing.
- **To switch:** hold the BOOT button for 1.5 s, say a voice command ("play everywhere", "bluetooth speaker"), or run
  `phrasehome speakers group kitchen`. The speaker restarts to switch, which takes about 3 seconds.

## Door camera (ESP32-CAM)

1. Plug the ESP32-CAM into the ESP32-CAM-MB board, then connect that to USB. In the Arduino IDE pick
   **AI Thinker ESP32-CAM**, and set `DOOR_NAME` in `config.h`.
2. Mount it at face height, about 0.5–1 m from where people stand, facing them, not the sun. Leave
   1–2 cm of clearance in front of the lens if it's behind a window.
3. In config.yaml set `door_scanner.camera: http://phrasehome-doorcam-front.local/capture`. If you set a
   `TOKEN`, add `?token=...` to that address.
4. Check it: open that address in a browser and you should see a photo.

**Night scanning:** the built-in flash LED (GPIO 4) works but is harsh. Better: an OV2640 "night vision"
lens (no IR filter) plus an 850 nm IR LED board switched by a transistor on a free pin. A side benefit is
that phone screens show up dark in IR, which makes spoofing with a photo on a phone harder.

**Power:** use a solid 5 V 2 A supply. Weak USB power is the #1 cause of ESP32-CAM resets.

## Protocol (if you want to build your own)

- **Mic → hub (UDP, port 7777):** `"PHM1"`, 1 byte room-name length, the room name, then 16 kHz 16-bit LE mono PCM
- **Hub → mic (UDP):** `"PHA1"` + 1 status byte (1 = heard, 2 = worked, 3 = failed)
- **Hub → speaker (UDP :7779):** `"PHS1"`, u32 seq, i64 play-at time in hub µs, 256 frames of 44.1 kHz 16-bit stereo PCM
- **Speaker → hub clock sync (UDP :7778):** `"PHT1"` + u64 speaker µs; the reply adds an i64 hub µs
- **Hub → speaker (HTTP):** `POST /api/mode?mode=bluetooth|group`, `POST /api/volume?level=|change=`, `GET /api/status`
- **Hub → device (HTTP):** `POST /api/set?channel=<name>&state=on|off|toggle&level=0-100`, header `X-Token`; `GET /api/status`
