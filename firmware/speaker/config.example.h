// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"

// Speaker name. Use the room name so voice commands with {here} find it.
// Shows up in your phone's Bluetooth list as "phrasehome <name>",
// and on the network as http://phrasehome-speaker-<name>.local
#define SPEAKER_NAME "kitchen"

// Optional shared secret, same as `token` for the speakers backend in config.yaml.
#define TOKEN ""

// Only accept group audio from this IP (your Pi). "" = accept from anyone on the network.
#define HUB_IP ""

// I2S amp / DAC wiring (MAX98357A or PCM5102A)
#define I2S_BCLK_PIN 26
#define I2S_LRC_PIN  25
#define I2S_DOUT_PIN 22

// Hold this button for 1.5 s to switch between Bluetooth and group mode.
// GPIO 0 is the BOOT button on most dev boards. -1 = no button.
#define MODE_BUTTON_PIN 0

// Status LED: slow blink = waiting, solid = playing / connected. -1 = none.
#define LED_PIN 2

// Max volume cap, 0-100, to protect small speakers.
#define MAX_VOLUME 100
