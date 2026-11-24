// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"

// Door name: reachable at http://phrasehome-doorcam-<DOOR_NAME>.local/capture
#define DOOR_NAME "front"

// Optional shared secret; the hub then uses .../capture?token=YOURTOKEN as its camera URL.
#define TOKEN ""

// Light for night scans. On the AI-Thinker ESP32-CAM, GPIO 4 is the bright white flash LED.
// For an IR camera, wire an 850 nm IR LED board (through a transistor) to a free pin instead.
#define LIGHT_PIN 4
// Turn the light on automatically when the picture is dark (0 = never, light is manual)
#define AUTO_LIGHT 0

// Picture: FRAMESIZE_VGA (640x480) is a good balance of detail and speed for face recognition.
#define FRAME_SIZE FRAMESIZE_VGA
#define JPEG_QUALITY 12   // 10 = best .. 63 = worst
