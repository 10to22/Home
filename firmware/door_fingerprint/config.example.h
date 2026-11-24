// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"

// Door name: reachable at http://phrasehome-finger-<DOOR_NAME>.local
#define DOOR_NAME "front"

// Shared secret for enrolling/deleting fingers (strongly recommended). Same as the hub's token.
#define TOKEN ""

// R503 wiring (ESP32 GPIO numbers): sensor TX -> FP_RX_PIN, sensor RX -> FP_TX_PIN,
// red wire -> 3V3, black -> GND, (touch/wakeup wire -> FP_TOUCH_PIN, optional).
#define FP_RX_PIN 16
#define FP_TX_PIN 17
