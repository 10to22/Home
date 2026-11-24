# phrasehome

An always-on listener for Google Home with **no wake word**. Say "turn on the lights",
"movie time" or "good night house" and it just happens. You don't need "Hey Google" first.

```
 mic ──► Vosk (offline speech-to-text, on the device) ──► phrase matcher ──► Google Assistant ──► your Google Home devices
```

- **Private by design.** Speech recognition runs on the device. Audio never leaves it.
  Only the command text (like `turn on the lights`) is sent to Google, and only when a phrase matches.
- **Works with everything in your Google Home app.** Commands go to Google Assistant as text,
  exactly as if you'd said "Hey Google, …". That covers lights, plugs, TVs, thermostats and routines.
- **Hard to trigger by accident.** The default `utterance` mode needs the whole thing you say to be the
  command. "Don't turn on the lights" is blocked. Low-confidence recognitions are ignored, and a cooldown
  stops double-fires.
- **Slots.** `turn on the {room} lights` covers every room with one line.
- **ESP32s all over the house.** Put a $8 ESP32 mic in each room and wire ESP32 device nodes to
  relays and LED strips. Say "lights on" and the lights *in that room* turn on. See [ESP32 setup](#esp32-setup).
- **Multi-room speakers.** ESP32 speaker modules work as Bluetooth speakers on their own, or join a
  group that plays the same music in sync in every room. See [Speakers](#speakers).
- **Door scanner.** A camera at the door recognizes faces. People you've allowed hear "retinal scan
  complete" and are asked for their fingerprint. Strangers and denied faces hear "retinal scan
  failed" and the door locks. See [Door scanner](#door-scanner).
- **Routines.** One phrase runs many actions: "lockdown", "good night house", "I'm leaving", "panic mode".
- **A voice you can customize.** Change any line the house says, record your own, or switch voices.
- **AI assistant.** "house, who came to the door today?" or "house, set things up for movie night".
  See [AI assistant](#ai-assistant).
- **Other backends.** Home Assistant, webhooks (IFTTT, Node-RED…) and local scripts.

## What you need

| Part | Notes |
|---|---|
| Raspberry Pi 4/5 (2 GB+) or any Linux box | A Pi 3B+ works with the small model |
| A USB microphone or mic array | A far-field array (e.g. ReSpeaker or a USB conference mic) hears you across a room much better |
| Power supply + SD card with Raspberry Pi OS (64-bit Lite is fine) | |

## Setup

### 1. Install on the Pi

```bash
git clone <this repo> phrasehome
```

```bash
cd phrasehome && ./deploy/install.sh
```

This installs dependencies, creates `.venv`, downloads the ~40 MB offline speech model into
`models/`, and copies `config.example.yaml` to `config.yaml`.

### 2. Connect it to your Google account (one time)

phrasehome uses the Google Assistant API to send commands as your account.

1. Go to <https://console.cloud.google.com/>, create a project, then go to **APIs & Services → Library** and
   enable **Google Assistant API**.
2. Go to **APIs & Services → OAuth consent screen**. Choose **External** and fill in the app name and your
   email. Under **Test users**, add the Google account your Google Home uses.
3. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**. Pick
   **Desktop app**, download the JSON, and save it in the repo folder as `client_secret.json`.
4. Authorize:

   ```bash
   .venv/bin/phrasehome auth-google
   ```

   It opens a browser so you can sign in, then writes `credentials.json`. On a headless Pi, run this step on
   your laptop instead (`pip install -e '.[google]'` there), then copy `credentials.json` to the Pi.

`credentials.json` and `client_secret.json` are git-ignored. Keep them private.

### 3. Pick your phrases

Edit `config.yaml`. Each command is a list of phrases plus what to tell Google:

```yaml
commands:
  - name: lights_on
    phrases: [turn on the lights, lights on]
    action: { say: turn on the lights }

  - name: room_lights_on
    phrases: ["turn on the {room} lights"]
    action: { say: "turn on the {room} lights" }   # {room} is filled in with what you said

  - name: movie_time
    phrases: [movie time]
    action: { say: start movie time }              # runs a Google Home routine
```

The `say` text is anything you'd normally say after "Hey Google". Use your device and room
names exactly as they appear in the Google Home app.

Check your phrases without a mic:

```bash
.venv/bin/phrasehome check
```

```bash
.venv/bin/phrasehome test "turn on the kitchen lights"
```

### 4. Try it, then make it permanent

```bash
.venv/bin/phrasehome run --dry-run
```

Speak a phrase. It prints what would run without touching your devices. Then drop `--dry-run` to run it
for real, and finally start it on boot:

```bash
sudo ./deploy/enable-service.sh
```

```bash
journalctl -u phrasehome -f
```

## ESP32 setup

```
 [ESP32 mic: kitchen] ─┐                          ┌─► [ESP32 device: kitchen]  relays / LED strip
 [ESP32 mic: bedroom] ─┼─ Wi-Fi audio ─► Pi hub ──┼─► [ESP32 device: bedroom]
 [Pi's own USB mic]   ─┘   (speech recognition)   └─► Google Home (TV, music, smart plugs, routines)
```

The ESP32s are too small to understand speech themselves, so the mics stream audio to the Pi and
the Pi does the recognition for every room. Each mic says which room it's in, so commands can use
`{here}`:

```yaml
- phrases: [lights on]
  action: { backend: esp, node: "{here}", channel: lights, state: on }
```

If two mics hear the same command, only the one that heard it most clearly fires.

1. Build and flash the boards: [firmware/README.md](firmware/README.md) has parts, wiring and flashing steps.
2. Start from the ESP32 example config:

   ```bash
   cp config.esp32.example.yaml config.yaml
   ```

3. List your nodes under `backends.esp.nodes`. Use the same names as `NODE_NAME` and `ROOM` in the firmware.
4. Run `phrasehome run --dry-run` and talk near a satellite. Its LED blinks when it hears a command.

A Pi 4 should handle several rooms. Each mic uses about 32 KB/s of Wi-Fi.

**Privacy note:** the mics stream audio continuously over your Wi-Fi to the Pi. It stays on your network
and is never stored, but it isn't encrypted, so keep the mics on a Wi-Fi network you trust (and use
`allowed_ips` so only your mics are accepted). Setting `TOKEN` on device nodes stops other devices on
the network from switching them.

## Speakers

Each ESP32 speaker module (`firmware/speaker`) has two modes:

```
 Separately:   phone ──Bluetooth──► [speaker: kitchen]          phone 2 ──Bluetooth──► [speaker: bedroom]

 Together:     phone ──Bluetooth──► Pi hub ──Wi-Fi, synced──┬─► [speaker: kitchen]
               (or Spotify Connect)                         ├─► [speaker: bedroom]
                                                            └─► [speaker: living room]
```

Bluetooth can only send audio from a phone to one speaker at a time, so for "together" your phone connects
to the Pi. The Pi relays the music over Wi-Fi with a timestamp on every packet. Each speaker keeps its clock
in step with the Pi and plays each packet at exactly that moment. In a simulation of the speaker's sync logic,
each speaker stayed within about 1 ms of the Pi's schedule, so rooms stay in time with each other.

1. Build the modules: [firmware/README.md](firmware/README.md#speaker-module).
2. Make the Pi a Bluetooth speaker your phone can connect to:

   ```bash
   ./deploy/setup-bluetooth-sink.sh
   ```

3. Add the `music:` backend and the speaker commands from `config.esp32.example.yaml` to your config.
4. Say "play everywhere", then play music on your phone connected to Bluetooth device **phrasehome**.

The voice commands include "play everywhere", "add this speaker", "bluetooth speaker", "louder" and "quieter".
They act on the speaker in the room you're in. You can do the same from the terminal:

```bash
.venv/bin/phrasehome speakers status
```

```bash
.venv/bin/phrasehome speakers group kitchen bedroom
```

```bash
.venv/bin/phrasehome speakers volume all 40
```

**Music sources for the group:** by default the Pi records whatever it's playing, which is how phone-over-Bluetooth works.
`source:` can be any command that outputs raw 16-bit, 44.1 kHz stereo audio. For example, Spotify Connect with
[librespot](https://github.com/librespot-org/librespot) (`librespot --name phrasehome --backend pipe`) lets you pick
"phrasehome" in the Spotify app with no Bluetooth needed. You can also play a file with ffmpeg:
`ffmpeg -i song.mp3 -f s16le -ar 44100 -ac 2 -`.

**Good to know**
- Group audio plays about half a second after your phone (Bluetooth delay plus the sync buffer). That's fine
  for music, but not for video.
- Each group speaker uses about 1.4 Mbit/s of Wi-Fi. If it stutters, raise `latency_ms` or move the router closer.
- Mics near speakers hear the music. Lyrics rarely match a whole command in `utterance` mode, but keep mic
  satellites a little away from the speakers.

## The whole smart home

`config.smarthome.example.yaml` wires everything together: mics, lights, locks, the door scanner,
speakers, Google Home devices (TV, vacuum, thermostat), phone alerts, the voice bank, routines and
the AI assistant. Start from it:

```bash
cp config.smarthome.example.yaml config.yaml
```

- **What to buy:** [docs/BUILD_LIST.md](docs/BUILD_LIST.md), with parts, costs, a 3-room budget and a safety checklist.
- **What to add next:** [docs/IDEAS.md](docs/IDEAS.md), from vacuums to fingerprint unlock to a morning briefing.

### Door scanner

```
 ESP32-CAM at the door ──photo──► Pi: find face ──► compare with the face bank
                                                     ├─ allowed  → "Retinal scan complete. Welcome home, Sam."
                                                     │             "Please place your finger on the scanner."  (door left as it is)
                                                     ├─ denied   → "Retinal scan failed."  → lock the door
                                                     └─ unknown  → add to the bank as visitor-N → "Retinal scan failed." → lock
```

It uses face recognition, because true retinal scanning needs medical-grade optics; see the
[build list](docs/BUILD_LIST.md#about-retinal-and-iris-scanning). The voice still says "retinal scan" because
you asked for that wording. You can change it in the voice bank. Every scan is logged with a snapshot.
A match never locks the door, so an unlocked door stays unlocked. The responses are lists of actions in
`door_scanner.on_allowed / on_denied / on_unknown`, so you can add phone alerts, porch lights or music.

```bash
.venv/bin/phrasehome faces enroll "Sam" --camera        # stand at the door, turn your head a little
```

```bash
.venv/bin/phrasehome faces enroll "Sam" --image sam1.jpg sam2.jpg
```

```bash
.venv/bin/phrasehome faces list                         # everyone, including auto-added visitors
```

```bash
.venv/bin/phrasehome faces allow visitor-3 --name "Grandma"
```

```bash
.venv/bin/phrasehome faces deny "Ex"                    # in the bank, but always refused
```

```bash
.venv/bin/phrasehome faces scans                        # recent scans with their photos
```

**Security:** a face alone can be fooled by a good photo, so the example config never unlocks on a face.
That's what the fingerprint stage is for (see IDEAS). Locks must always open from the inside without power;
see the safety checklist in the build list.

### Routines

A routine is a list of steps, and any action can be a step. Here is "lockdown" from the example:

```yaml
routines:
  lockdown:
    - { door: all, state: locked }
    - { node: kitchen, channel: lights, state: off }
    - { node: living room, channel: lights, state: off }
    - { node: porch, channel: lights, state: on }     # keep the porch lit
    - { speak: lockdown }
commands:
  - { phrases: [lockdown, lock down], action: { routine: lockdown } }
```

Steps can also be `{ wait: 20 }` or another `{ routine: ... }`. The example includes `lockdown`, `goodnight`,
`leaving` (locks up after 20 s, then starts the vacuum), `good_morning`, `movie_time`, `party` and `panic`.

### Voice bank

Everything the house says has a name: `retinal_scan_complete`, `retinal_scan_failed`, `lockdown`,
and so on. You can customize it in three ways:

1. **Words:** `voice.phrases` in the config. Give a list to vary it, and use `{name}` or `{door}` placeholders.
2. **Your own recording:** run the command below. It saves `voice_bank/<name>.wav`, which then plays instead
   of the synthesized voice. A folder `voice_bank/<name>/` of several clips picks one at random.

   ```bash
   .venv/bin/phrasehome voice record retinal_scan_complete
   ```

3. **The voice itself:** `engine: piper` (natural and offline; [many voices](https://huggingface.co/rhasspy/piper-voices)),
   `espeak` (robotic), or `command` to use any tool, for example a cloned voice.

```bash
.venv/bin/phrasehome voice list
```

```bash
.venv/bin/phrasehome voice say retinal_scan_complete
```

Speech plays on the Pi's speaker and, if `voice.speakers` is set, through every speaker in group mode.

### AI assistant

Say the wake name, then anything:

- "house, what's the weather this weekend?"
- "house, who came to the door while I was out?"
- "house, it's movie night"
- "house, is the back door locked?"

When it hears its name, the Pi re-transcribes your whole sentence as free speech and sends the text to
Claude (`claude-opus-5-5`), along with your commands, routines and doors as tools it can use and the
house's event log. It can search the web, and its reply is spoken. Commands without the name still run
instantly and offline. The AI is only for things you ask it.

1. Get an API key at console.anthropic.com and add it to `.env`:

   ```bash
   echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
   ```

2. Add the `assistant:` section from the example config.
3. Try it by typing:

   ```bash
   .venv/bin/phrasehome ask --dry-run "lock everything up for the night"
   ```

**Built-in limits:**
- It can lock doors but never unlock them, unless you set `allow_unlock: true`.
- It won't tell someone outside who is enrolled in the face bank.
- Only the words after its name are sent to Anthropic, never audio.
- Each question costs a few cents of API usage.

For better understanding of free speech, set `transcriber: whisper` after `pip install faster-whisper`.

## Tuning

| Problem | Fix |
|---|---|
| It fires when you didn't mean it | Raise `match_threshold` (e.g. 90) or `min_confidence`, keep `match_mode: utterance`, use longer phrases |
| It misses you | Set `log_heard: true` to see what it hears. Lower `match_threshold`, add phrase variants, or use a better mic |
| You want commands inside sentences ("ok so lights on please") | `match_mode: contains` (more false triggers) |
| Wrong microphone | `phrasehome devices`, then set `device:` to the index |
| Accuracy is bad with unusual words | Keep `restrict_vocabulary: true`, or try a bigger model from <https://alphacephei.com/vosk/models> (set `model_path`) |
| Google replies but nothing happens | Make sure `say` works when spoken to a Google speaker. In the Google Home app, make sure **Personal results** is on for your account |

## Other backends

```yaml
backends:
  home_assistant:
    type: home_assistant
    url: http://homeassistant.local:8123
    token_env: HA_TOKEN        # long-lived token, set in the environment or deploy/.env
  ifttt:
    type: webhook
    url: https://maker.ifttt.com/trigger/EVENT/with/key/KEY
  local:
    type: shell

commands:
  - phrases: [porch light on]
    action: { backend: home_assistant, service: light.turn_on, data: { entity_id: light.porch } }
  - phrases: [garage]
    action: { backend: home_assistant, say: open the garage }   # Home Assistant conversation agent
  - phrases: [play the doorbell]
    action: { backend: local, command: [aplay, /home/pi/ding.wav] }
```

## Development

```bash
pip install -e '.[dev]'
```

```bash
pytest
```

Code layout: `phrasehome/matcher.py` (phrase expansion and fuzzy matching), `listener.py` (mics and Vosk),
`satellites.py` (ESP32 mic receiver), `dispatcher.py` (cooldown, multi-mic arbitration, background execution),
`speakers.py` (synced multi-room streaming), `faces.py` + `door_scanner.py` (face bank and scan
decisions), `doors.py`, `voice.py`, `assistant.py`, `events.py`, `actions/` (backends, including routines),
`firmware/` (ESP32 sketches).

## Caveats

- Google treats the Assistant API as a developer/prototype API, and the Assistant itself is changing as
  Google moves to Gemini. If the Google backend stops working, the Home Assistant backend (with Google Home
  devices linked into Home Assistant) is the fallback.
- An always-on mic hears everything. Recognition is local and nothing is stored or uploaded, but tell people
  in your home it's there.
