// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"

// The hub's dashboard address (an IP is most reliable for a battery device) and token.
#define HUB_HOST  "192.168.1.10"
#define HUB_PORT  8800
#define HUB_TOKEN ""            // same as PHRASEHOME_TOKEN on the hub, if set

// Lid sensor on an RTC-capable pin (0, 2, 4, 12-15, 25-27, 32-39).
// Reed switch + magnet: with the lid shut the switch is closed (pin LOW with the pull-up);
// opening the lid pulls the magnet away -> pin goes HIGH -> wake up.
#define LID_PIN 33
#define LID_OPEN_LEVEL 1        // pin level when the lid is open

// Battery voltage through a 100k/100k divider to an ADC pin (-1 = no battery reading)
#define BATTERY_PIN 35
