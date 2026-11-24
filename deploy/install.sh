#!/usr/bin/env bash
# Install phrasehome on a Raspberry Pi / Debian / Ubuntu box.
# Run from the repo root:  ./deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL=vosk-model-small-en-us-0.15

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libportaudio2 unzip curl ffmpeg espeak-ng alsa-utils

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[audio,google,vision,ai,calendar]'
# Natural offline voice. If it won't install on this machine, set `voice: {engine: espeak}`.
.venv/bin/pip install -e '.[voice]' || echo "piper-tts didn't install; use engine: espeak"

if [ ! -d "models/$MODEL" ]; then
  mkdir -p models
  curl -L -o "models/$MODEL.zip" "https://alphacephei.com/vosk/models/$MODEL.zip"
  unzip -q "models/$MODEL.zip" -d models
  rm "models/$MODEL.zip"
fi

# Face detection + recognition models (OpenCV Zoo, ~40 MB)
mkdir -p models/face
for m in face_detection_yunet/face_detection_yunet_2023mar.onnx \
         face_recognition_sface/face_recognition_sface_2021dec.onnx; do
  f="models/face/$(basename "$m")"
  [ -s "$f" ] || curl -L -o "$f" "https://github.com/opencv/opencv_zoo/raw/main/models/$m"
done

# Camera person/vehicle/animal detector (MobileNet-SSD, ~23 MB)
mkdir -p models/detect
D=https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master
[ -s models/detect/deploy.prototxt ] || curl -fL -o models/detect/deploy.prototxt "$D/deploy.prototxt" \
  || echo "!! couldn't download the camera detector; see README (Cameras)"
[ -s models/detect/mobilenet_iter_73000.caffemodel ] || curl -fL -o models/detect/mobilenet_iter_73000.caffemodel \
  "https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel" || true

# Voice recognition (who's talking), ~13 MB
if [ ! -d models/vosk-model-spk-0.4 ]; then
  curl -L -o models/spk.zip https://alphacephei.com/vosk/models/vosk-model-spk-0.4.zip \
    && unzip -q models/spk.zip -d models && rm models/spk.zip
fi

# Piper voice (pick others at https://huggingface.co/rhasspy/piper-voices)
mkdir -p models/piper
V=https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
[ -s models/piper/en_US-lessac-medium.onnx ] || curl -L -o models/piper/en_US-lessac-medium.onnx "$V"
[ -s models/piper/en_US-lessac-medium.onnx.json ] || curl -L -o models/piper/en_US-lessac-medium.onnx.json "$V.json"

[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || { cat > .env <<'ENV'
# Secrets for the hub (keep this file private)
# ANTHROPIC_API_KEY=...        AI assistant
# PHRASEHOME_TOKEN=...         dashboard + ESP32 token
# GOOGLE_MAPS_KEY=...          traffic (Routes API)
# TRACK17_KEY=...              package tracking (17TRACK)
# SPOTIFY_CLIENT_ID=...        Spotify control
# SPOTIFY_CLIENT_SECRET=...
ENV
chmod 600 .env; }

# Spotify Connect speaker ("House"): librespot. The raspotify package includes it:
#   curl -sL https://dtcooper.github.io/raspotify/install.sh | sh && sudo systemctl disable --now raspotify

echo
echo "Installed. Next:"
echo "  1. Put your Google OAuth client file here as client_secret.json (see README)"
echo "  2. .venv/bin/phrasehome auth-google"
echo "  3. .venv/bin/phrasehome run --dry-run     # try it without controlling anything"
echo "  4. sudo ./deploy/enable-service.sh        # start on boot"
echo
echo "Whole smart home: start from config.smarthome.example.yaml (see README)."
