#!/usr/bin/env bash
# Install phrasehome on a Raspberry Pi / Debian / Ubuntu box.
# Run from the repo root:  ./deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL=vosk-model-small-en-us-0.15

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libportaudio2 unzip curl

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e '.[all]'

if [ ! -d "models/$MODEL" ]; then
  mkdir -p models
  curl -L -o "models/$MODEL.zip" "https://alphacephei.com/vosk/models/$MODEL.zip"
  unzip -q "models/$MODEL.zip" -d models
  rm "models/$MODEL.zip"
fi

[ -f config.yaml ] || cp config.example.yaml config.yaml

echo
echo "Installed. Next:"
echo "  1. Put your Google OAuth client file here as client_secret.json (see README)"
echo "  2. .venv/bin/phrasehome auth-google"
echo "  3. .venv/bin/phrasehome run --dry-run     # try it without controlling anything"
echo "  4. sudo ./deploy/enable-service.sh        # start on boot"
