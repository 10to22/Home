// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"

// The Pi running phrasehome. An IP address is most reliable (give the Pi a fixed
// DHCP lease in your router). A name ending in ".local" is looked up over mDNS.
#define HUB_HOST "raspberrypi.local"
#define HUB_PORT 7777

// The room this mic is in. Commands can use {here} to act on this room.
// Use the same name as your device node / Google Home room, e.g. "kitchen", "living room".
#define ROOM "kitchen"

// INMP441 wiring (ESP32 GPIO numbers)
#define I2S_SCK_PIN 26   // INMP441 SCK
#define I2S_WS_PIN  25   // INMP441 WS
#define I2S_SD_PIN  33   // INMP441 SD
// INMP441 L/R pin to GND = left channel. If the hub hears only silence, set this to 1.
#define MIC_USE_RIGHT_CHANNEL 0

// Loudness. Lower = louder (each step down doubles volume). 14 suits most rooms;
// try 12-13 for a big room or a far-away mic, 15-16 if it sounds distorted.
#define GAIN_SHIFT 14

// Status LED (GPIO 2 on most ESP32 dev boards; set -1 for none)
// Solid = muted. Short blink = heard a command, long = done, 3 flashes = failed.
#define LED_PIN 2

// Mute button: press to stop sending audio (hard privacy switch). GPIO 0 = BOOT button. -1 = none.
#define MUTE_BUTTON_PIN 0

// Optional speaker (MAX98357A) so this room module can talk: reminders, timers, AI answers.
// Needs a classic ESP32 or ESP32-S3 (two I2S ports). Set to 0 if there's no speaker.
#define SPEAKER_ENABLED 1
#define SPK_BCLK_PIN 27
#define SPK_LRC_PIN  14
#define SPK_DIN_PIN  13
