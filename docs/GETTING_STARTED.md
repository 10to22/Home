# Getting it all going before you have the place

You can build and test almost everything before you move in: first on a laptop with no hardware
at all, then on a desk with one of each module, then a full rehearsal of every room. On move-in day
you're mostly plugging things in and mounting them.

| Stage | Where | What you need | You'll have |
|---|---|---|---|
| [0. Laptop](#stage-0-laptop-no-hardware) | Any computer | Nothing to buy | Your whole config running as a virtual house you can click and talk to |
| [1. Accounts](#stage-1-accounts-and-keys) | Online | A card for the paid ones | AI, Google, phone alerts, Spotify, traffic, packages all linked |
| [2. Desk kit](#stage-2-the-desk-kit-one-of-each) | A desk | The [starter kit](EQUIPMENT.md#phase-1-desk-starter-kit-buy-now) | Every kind of module proven working |
| [3. Rehearsal](#stage-3-full-rehearsal) | A desk or a garage | The [core house](EQUIPMENT.md#phase-2-core-house-before-move-in) parts | Every module built, labeled and burned in for a week |
| [4. Planning](#stage-4-planning-the-new-place) | Paper / the listing | The floor plan | A map of what goes where, and what an electrician must do |
| [5. Move-in week](#stage-5-move-in-week) | The new place | Tools, an afternoon per area | Running |
| [6. Tuning](#stage-6-the-first-two-weeks-tuning) | The new place | Patience | Everything dialed in to the real rooms |

---

## Stage 0: Laptop, no hardware

### 0.1 Get the code running

You need Python 3.10+ and git (macOS: `brew install python git`; Windows: use WSL with Ubuntu).

```bash
git clone https://github.com/10to22/Home.git phrasehome
```

```bash
cd phrasehome && git checkout claude/sharp-ptolemy-mw7xsz
```

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,vision,ai,calendar]'
```

```bash
.venv/bin/pytest -q
```

All tests should pass (116 at the time of writing).

### 0.2 Write your real config now

```bash
cp config.smarthome.example.yaml config.yaml
```

Edit `config.yaml` with the people and rooms you'll have. Room names you choose now are the names the
ESP32s get later, so pick them once (keep them simple: `kitchen`, `living room`, `bedroom`).
Then check it:

```bash
.venv/bin/phrasehome check
```

```bash
.venv/bin/phrasehome test "turn on the kitchen lights"
```

`test` shows which command a sentence matches (or "no match"). Try the way each person in the house
naturally talks, and add phrases until everything matches.

### 0.3 Run the virtual house

```bash
.venv/bin/phrasehome simulate
```

It prints a link for each simulated ESP32 device node plus the dashboard
(`http://localhost:8800`). Open the dashboard and one or two node pages side by side, then try:

| Try this | Where | You should see |
|---|---|---|
| Type `lights on`, room `kitchen` | Dashboard → Say or ask → "Type a command" | The kitchen node page: **lights ON** |
| Type `lockdown` | Dashboard | Front door → locked, lights off, porch on (node pages) |
| Toggle **contact** off on the *front door* node | Node page | Dashboard activity: `sensor front door contact off` |
| Toggle **presence** on the *kitchen* node (in the config, set `only_dark: false` to test in daylight) | Node page | Kitchen lights follow you |
| Toggle **leak** on the *kitchen* node | Node page | Activity: `automation water leak`; the log says what it would announce |
| On the *garage* node toggle **closed** off (door open), then **car** on, and wait 5 min | Node page | The garage routine pulses the opener to close it |
| Put **keys** "here" on the *bedroom* node, then type `where are my keys` | Node page + dashboard | "Sam's keys are in the bedroom" (in the log); a key on the floor plan |
| Slide **water bowl** below 20 on the *kitchen* node | Node page | "Luna's water bowl is almost empty" |
| Type `set a timer for one minute`, room `kitchen` | Dashboard | A ⏱ under Household, and a minute later the timer rings (in the log) |
| Type `arm the alarm`, wait 90 s (exit delay), then toggle the front door contact off | Dashboard + node | Alarm: arming → armed → entry; 60 s later: alert (type `disarm` before then to cancel) |
| Open **Meal planner** | Dashboard | Plan a week, add pantry items, watch the shopping list update |
| Add a countdown and a package | Dashboard | Cards fill in |
| Drag rooms in **House → edit layout** | Dashboard | Arrange the floor plan like the new place, then save |

Online services (Google, phone alerts, speakers, Home Assistant) are replaced by the log during a
simulation. Nothing real is touched. Not simulated: smart plugs, speakers, cameras (unless `--webcam`)
and the fingerprint sensor.

### 0.4 Talk to it (laptop microphone)

1. Download the speech model: <https://alphacephei.com/vosk/models> → `vosk-model-small-en-us-0.15`,
   unzip into `models/`.
2. Install audio support. macOS: `brew install portaudio`. Ubuntu/WSL: `sudo apt install libportaudio2 espeak-ng ffmpeg`.
   Then `.venv/bin/pip install -e '.[audio]'`.
3. In config.yaml, `sources: local_mic: { room: living room }` is the room your laptop pretends to be.
4. Run it:

   ```bash
   .venv/bin/phrasehome simulate --mic
   ```

   Then say "lights on", "set a timer for one minute", "lockdown". Set `log_heard: true` to see what it hears.

### 0.5 The door scanner with your webcam

1. Download the face models into `models/face/` (the two `curl` lines in `deploy/install.sh`).
2. In config.yaml, temporarily set `door_scanner: camera: 0`.
3. Enroll yourself:

   ```bash
   .venv/bin/phrasehome faces enroll "Your Name" --camera
   ```

4. Run with the webcam:

   ```bash
   .venv/bin/phrasehome simulate --webcam
   ```

   Look at the camera and you'll hear "Retinal scan complete. Welcome, Your Name." Have a friend try:
   they get "retinal scan failed" and land in the face bank as visitor-1 (`phrasehome faces list`).

### 0.6 The voice and the AI

- Voice: `engine: espeak` works everywhere. For the natural voice, `pip install piper-tts` and download a
  voice (see install.sh). Try `phrasehome voice say retinal_scan_complete`. Record your own lines now
  with `phrasehome voice record <name>`.
- AI: put `ANTHROPIC_API_KEY=...` in `.env`, then `.venv/bin/phrasehome ask "what can you do?"`, or type
  `house, plan dinners for next week` in the dashboard during `simulate`.

---

## Stage 1: Accounts and keys

None of these depend on the house. Set them up now and test each one.

| Service | For | Steps | Test |
|---|---|---|---|
| **Anthropic API** | AI assistant, camera questions, meals, diary | console.anthropic.com → API key → `.env`: `ANTHROPIC_API_KEY=` | `phrasehome ask "hello"` |
| **Google Assistant API** | Google Home devices | README → "Connect it to your Google account" | `phrasehome auth-google`, then `simulate --real-backends`, type `tv on`: the log shows Google's reply (even "I can't find a TV" proves the link works) |
| **ntfy** (free) | Phone alerts with photos | Install the ntfy app, subscribe to a long random topic; put it in `phone: topic:` | `curl -d "hello" ntfy.sh/<your-topic>` |
| **Spotify** | Music by voice | Premium account; developer.spotify.com → create an app, redirect URI `http://127.0.0.1:8888/callback`; id and secret in `.env` | `phrasehome auth-spotify`; play something on your phone, then type `what song is this` with `--real-backends` |
| **Google Maps (Routes API)** | Traffic and leave-now nudges | console.cloud.google.com → enable Routes API → API key (restrict it to Routes) → `.env`: `GOOGLE_MAPS_KEY=` | Put your real work/school addresses in `commute:`; type `how's traffic` |
| **17TRACK** | Package tracking | api.17track.net → key → `.env`: `TRACK17_KEY=` | `phrasehome package add <a real tracking number>`, then `phrasehome package` |
| **Weather** | Forecasts, alerts | Free, no key: set `location:` to the new address's latitude/longitude | Type `what's the weather` |

Keep `.env` private and backed up (it's ignored by git).

---

## Stage 2: The desk kit (one of each)

Buy the [Phase 1 starter kit](EQUIPMENT.md#phase-1-desk-starter-kit-buy-now). It's all reused in the
house later, so nothing is wasted.

### 2.1 Set up the network you'll keep

ESP32s remember the Wi-Fi name and password they're flashed with. Pick a dedicated **2.4 GHz** network
name and password for the house's devices now, e.g. `phrasehome-iot`. Either:
- run it on a small travel router (GL.iNet) that you'll plug into the new place's internet, or
- set up the same name and password on the new router later.

Either way you never reflash anything when you move.

### 2.2 Set up the Pi hub

1. Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)**. In its settings, set the hostname to
   `phrasehome`, enable SSH, and enter your Wi-Fi (or use Ethernet).
2. Boot it, then connect:

   ```bash
   ssh pi@phrasehome.local
   ```

3. Install:

   ```bash
   git clone https://github.com/10to22/Home.git phrasehome && cd phrasehome && git checkout claude/sharp-ptolemy-mw7xsz && ./deploy/install.sh
   ```

4. Copy your `config.yaml` and `.env` from the laptop, then try a dry run:

   ```bash
   .venv/bin/phrasehome run --dry-run
   ```

5. When happy, start it for real on boot:

   ```bash
   sudo ./deploy/enable-service.sh
   ```

### 2.3 Install the Arduino tools (on your laptop)

Arduino IDE 2 → Boards Manager: **esp32 by Espressif**. Library Manager: **Adafruit Fingerprint Sensor
Library**, **IRremoteESP8266**. ZIP libraries for the speaker: **ESP32-A2DP** and **arduino-audio-tools**.
For each sketch: copy `config.example.h` to `config.h` and fill in the Wi-Fi from 2.1 and the room name.

### 2.4 Test each module

Do one at a time, and check the result before moving on. Wiring for each is in [firmware/README.md](../firmware/README.md).

**Room module** (`mic_satellite`, with the speaker)

| Check | Expected |
|---|---|
| Serial Monitor after flashing | `Wi-Fi OK`, then `Hub: 192.168.x.x:7777, room: kitchen` |
| Hub log (`journalctl -u phrasehome -f`) | `satellite connected: kitchen` |
| Dashboard → Devices | `mic: kitchen · online · speaker` |
| `log_heard: true`, say "lights on" | Log: `heard [kitchen]: 'lights on'` and the LED blinks |
| Type `what time is it` (room kitchen) on the dashboard | The module speaks the time |
| Press BOOT | LED stays on (muted); Devices shows `muted` |
| Press the test button on a real smoke alarm near it | Activity: `smoke.alarm kitchen` (it's loud, so cover your ears) |
| If it hears poorly | Change `GAIN_SHIFT` (lower = louder); try `MIC_USE_RIGHT_CHANNEL 1` if it hears nothing |

**Device node** (`device_node`). On a desk, use LEDs with 220 Ω resistors in place of relays.

| Check | Expected |
|---|---|
| Open `http://phrasehome-kitchen.local` | The node's page with a button per channel |
| Type `lights on` (room kitchen) | The LED on the lights pin turns on |
| Reed switch + magnet as `contact`: pull the magnet away | Activity: `sensor kitchen contact off` |
| Wave at the LD2410 / PIR | `sensor kitchen presence on` |
| SHT40 (`CLIMATE_SENSOR 4`) | Type `what's the temperature` and hear it |
| `ENABLE_BLE_SCAN` + a Bluetooth tag nearby | `/api/status` lists the tag's MAC; put that MAC in `tags:`; `where are my keys` works |
| IR (`IR_SEND_PIN` / `IR_RECV_PIN`) | `phrasehome ir learn tv_power --node kitchen` with your TV remote, then `tv power` switches the TV |

**Speaker module** (`speaker`)

| Check | Expected |
|---|---|
| Phone Bluetooth list | `phrasehome kitchen`; pair and play music |
| `phrasehome speakers group kitchen` | It restarts into group mode |
| `./deploy/setup-bluetooth-sink.sh` on the Pi, phone connects to `phrasehome`, play music | It plays on the speaker in group mode |
| Spotify app → devices → `House` (with librespot) | Plays on the group |

**Door camera + fingerprint** (`door_cam`, `door_fingerprint`)

| Check | Expected |
|---|---|
| `http://phrasehome-doorcam-front.local/capture` | A photo |
| `phrasehome faces enroll "Sam" --camera` | `enrolled Sam ... allowed` |
| Sam walks up to the camera | "Retinal scan complete. Welcome, Sam. Please place your finger on the scanner." |
| `phrasehome fingerprint enroll "Sam"` | Ring LED guides two presses, then `saved as finger #1` |
| Face, then finger within 30 s | "Fingerprint accepted…", and the `lock` channel turns off (an LED or 12 V solenoid stands in for the lock) |
| A stranger's face | "Retinal scan failed", a lock, and a visitor photo on the dashboard |
| Press the doorbell button (GPIO 13) | Chime + announcement + phone notification with the photo |

**Extras**

| Module | Desk test |
|---|---|
| Mailbox | Separate the reed switch and magnet → within ~10 s "you've got mail" (it reconnects to Wi-Fi each time) |
| Parking | Move a box toward the sensor: green → yellow → red → flashing red; `car` on in its status |
| Pet door | Hold a collar tag to the reader → the servo opens, `petdoor.used` in activity; an unknown tag doesn't open it |
| Smart plug (Shelly) | Plug a lamp or kettle in → watts on the dashboard; switch a hair dryer on for 1 min then off → `appliance.done` |
| IP camera | Walk in front of it → `camera person` with a snapshot; type `house, what do you see on the driveway camera?` |

---

## Stage 3: Full rehearsal

1. Build every module for every room from the [Phase 2 list](EQUIPMENT.md#phase-2-core-house-before-move-in).
2. **Label each one** (masking tape or a label maker): room name, what it is, IP address.
3. Keep a sheet of every device: room, MAC address, IP, what's wired to which pin. A
   spreadsheet is fine; the dashboard's Devices card shows what's online.
4. Run the whole house on the desk for a week with the real config. Flaky boards and weak power supplies
   show up here, not after they're glued behind a TV.
5. Run `phrasehome backup` and keep the file somewhere safe (it holds face data and keys).

---

## Stage 4: Planning the new place

Do this with the listing's floor plan, before move-in day.

**Rooms and modules.** Mark where each room module, device node, speaker, camera and sensor goes.
Update `instances:`, `rooms:`, the floor plan layout and `commute:` addresses. Get the latitude/longitude
of the address for `location:`.

**Wi-Fi.** ESP32s need 2.4 GHz and reach about one or two rooms through walls. Plan the router (or
mesh) near the middle of the house. Bring a laptop on a walkthrough and check the signal in the garage,
by the mailbox and at the doors.

**Power.** Every module needs a USB outlet or a 5/12 V supply nearby. Check outlets near: the front door
(camera, fingerprint, lock), each room's module spot, the windows with blinds, the garage wall (parking),
the TV (IR), and outside (cameras).

**Doors and locks.**
- **Renting?** Ask before changing locks. A smart deadbolt that keeps the existing key and only replaces
  the inside part is usually acceptable.
- **Check the door:** deadbolt hole size (standard is 2-1/8 in / 54 mm) and backset.
- **Exits:** every lock must open from the inside by hand, and you keep a physical key.

**Book an electrician for** any mains work: an electric strike wired into the frame, a dryer or other
240 V energy monitor (a Shelly EM with a clamp), hardwired dishwashers, outdoor outlets for cameras,
replacing light switches.

**Smoke alarms.** Check the alarms are standard ones that beep in the temporal-three pattern (most are)
and that CO alarms are present. The listener will hear them.

**Internet.** Book the install so it's live on move-in day, because the hub, AI and phone alerts need it.

**Addresses.** Set DHCP reservations on the router for the Pi and the cameras, so their addresses never
change.

---

## Stage 5: Move-in week

Do it in this order, testing each step on the dashboard before the next:

1. **Internet + router** with the same IoT network name and password as on the desk.
2. **Pi hub + UPS**, central and next to the router. Check the dashboard opens.
3. **Front door:** lock, door camera at face height (~1.5 m, 0.5 to 1 m from where people stand, not into
   the sun), fingerprint sensor beside it, contact sensor on the frame. Re-enroll faces at the actual door
   in day and night light.
4. **Room modules**, one per room: central, about 1 to 1.5 m high, away from the TV and speakers.
5. **Device nodes and sensors:** lights, mmWave sensors (a corner, ~2 m high, facing where people
   sit), door and window contacts, SHT40 away from windows and heaters.
6. **Speakers.**
7. **Cameras** outside: 2.5 to 3 m high, angled down, covering the paths to the doors.
8. **Extras:** mailbox, parking (park once and set `STOP_CM`), pet door, blinds, bed mats, plugs.

---

## Stage 6: The first two weeks: tuning

| If… | Change |
|---|---|
| Commands fire by accident | Raise `match_threshold` (85 → 90) or `min_confidence`; use longer phrases |
| Commands are missed | `log_heard: true`, add the phrases people actually say; lower `GAIN_SHIFT` for far mics |
| The face scan is slow or misses | Lower `min_face_px` a little; enroll more samples at the door in both day and night light |
| Strangers ever match someone | Raise `match_threshold` (0.40 → 0.45) |
| Camera alerts for leaves, shadows or cars passing | Raise `camera_settings.min_confidence`; limit `detect_groups` per camera |
| Lights switch off while you sit still | Raise `rooms.empty_after`; mmWave instead of PIR |
| Blinds stop short | Time a full open and set the cover's travel seconds |
| Washer "done" fires mid-cycle | Watch its watts on the dashboard during a cycle; raise `done_after` |
| The alarm feels too strict | Raise `entry_delay`; remove motion from `watch` |
| The AI costs more than you'd like | `watcher: every_minutes: 180`, `effort: low`, fewer `ask:` steps |
| Quiet time in kids' rooms | `quiet_hours` per instance |

## What really can't be tested until you're there

Wi-Fi coverage, real lighting at the door, camera angles, how mmWave sensors see each room, blinds
motors, bed mats under the actual mattress, lock fitting, and the house's power quirks. Everything else
you can have working before you get the keys.
