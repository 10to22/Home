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

Code layout: `phrasehome/matcher.py` (phrase expansion and fuzzy matching), `listener.py` (mic and Vosk),
`dispatcher.py` (cooldown and background execution), `actions/` (backends).

## Caveats

- Google treats the Assistant API as a developer/prototype API, and the Assistant itself is changing as
  Google moves to Gemini. If the Google backend stops working, the Home Assistant backend (with Google Home
  devices linked into Home Assistant) is the fallback.
- An always-on mic hears everything. Recognition is local and nothing is stored or uploaded, but tell people
  in your home it's there.
