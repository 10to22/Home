#!/usr/bin/env bash
# Make the Pi a Bluetooth speaker called "phrasehome", so your phone can stream to it and the
# hub can pass the music on to every ESP32 speaker in group mode.
# Run as your normal user (not root):  ./deploy/setup-bluetooth-sink.sh
#
# Note: pairing is automatic with no PIN, like most Bluetooth speakers. Anyone in range can
# connect while it's discoverable. Run `bluetoothctl discoverable off` once your devices are paired.
set -euo pipefail
NAME=${1:-phrasehome}

sudo apt-get update
sudo apt-get install -y pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth \
  pulseaudio-utils bluez bluez-tools

# Run the audio server even when nobody is logged in.
sudo loginctl enable-linger "$USER"
systemctl --user enable --now pipewire pipewire-pulse wireplumber

# Present the Pi as an audio speaker that is always discoverable.
sudo sed -i \
  -e "s/^#\?\s*Name\s*=.*/Name = $NAME/" \
  -e "s/^#\?\s*Class\s*=.*/Class = 0x200414/" \
  -e "s/^#\?\s*DiscoverableTimeout\s*=.*/DiscoverableTimeout = 0/" \
  -e "s/^#\?\s*PairableTimeout\s*=.*/PairableTimeout = 0/" \
  /etc/bluetooth/main.conf

# Auto-accept pairing (no PIN) and stay discoverable.
sudo tee /etc/systemd/system/bt-agent.service >/dev/null <<UNIT
[Unit]
Description=Bluetooth auto-pair agent for phrasehome
After=bluetooth.service
Requires=bluetooth.service

[Service]
ExecStartPre=/usr/bin/bluetoothctl power on
ExecStartPre=/usr/bin/bluetoothctl discoverable on
ExecStartPre=/usr/bin/bluetoothctl pairable on
ExecStart=/usr/bin/bt-agent -c NoInputNoOutput
Restart=always

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo systemctl enable --now bt-agent

echo
echo "Done. On your phone, connect to Bluetooth device \"$NAME\"."
echo "Check the hub hears it:  parec --raw --format=s16le --rate=44100 --channels=2 -d @DEFAULT_MONITOR@ | head -c 200000 | wc -c"
