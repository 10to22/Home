// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"

// Node name: reachable at http://phrasehome-<NODE_NAME>.local
// Match the node name in your phrasehome config (and the ROOM of the mic in this room).
#define NODE_NAME "kitchen"

// Optional shared secret. If set, the hub must send it (token / token_env in config.yaml).
#define TOKEN ""

// What's connected. One line per thing:
//   { "name", output pin, dimmable, active_low, button pin (-1 = none) }
// - dimmable:   true for MOSFET-driven LED strips/lamps (PWM, 0-100%), false for relays
// - active_low: true for most cheap relay boards (they switch on when the pin goes LOW)
// - button:     optional push button to GND that toggles this channel by hand
#define CHANNEL_LIST                         \
  { "lights", 21, false, true, 19 },         \
  { "lamp",   22, true,  false, -1 },        \
  { "fan",    23, false, true, -1 }
// Safe output pins on a classic ESP32: 4, 13, 14, 18, 19, 21, 22, 23, 25, 26, 27, 32, 33.
// Avoid 6-11 (flash), 34-39 (input only), and 16/17 on WROVER boards (PSRAM).

// Sensors (optional). One line per input, each ending with a comma:
//   { "name", pin, type, active_low },
// type: DIGITAL (contact/reed switch, PIR or mmWave motion, leak probe, button, doorbell)
//       ANALOG (0-100%: light sensor, potentiometer) / ANALOG_INVERTED (soil moisture: wet = high %)
// active_low: true for switches wired to GND (uses the internal pull-up)
// Analog inputs must use ADC1 pins (32-39): ADC2 doesn't work while Wi-Fi is on.
#define INPUT_LIST                              \
  { "contact", 25, DIGITAL, true },             \
  { "motion",  26, DIGITAL, false },            \
  { "button",  27, DIGITAL, true },
//  { "leak",    33, DIGITAL, true },
//  { "soil",    34, ANALOG_INVERTED, false },
