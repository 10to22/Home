# ESP32 firmware

Two sketches, one per board:

| Sketch | What it does | Parts (approx. cost) |
|---|---|---|
| `mic_satellite/` | Mic in a room. Streams audio to the hub, blinks its LED when a command is heard | ESP32 dev board + INMP441 I2S mic (~$8) |
| `device_node/` | Switches relays and dims LED strips on command. Has its own web page with buttons | ESP32 dev board + relay module and/or MOSFET module (~$6–10) |

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

## Protocol (if you want to build your own)

- **Mic → hub (UDP, port 7777):** `"PHM1"`, 1 byte room-name length, the room name, then 16 kHz 16-bit LE mono PCM
- **Hub → mic (UDP):** `"PHA1"` + 1 status byte (1 = heard, 2 = worked, 3 = failed)
- **Hub → device (HTTP):** `POST /api/set?channel=<name>&state=on|off|toggle&level=0-100`, header `X-Token`; `GET /api/status`
