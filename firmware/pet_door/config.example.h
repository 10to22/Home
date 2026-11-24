// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"
#define DOOR_NAME "petdoor"        // http://phrasehome-petdoor.local
#define TOKEN ""

// Which collar tags open the door: { "pet name", "tag id" } (the id is printed to Serial
// when you hold a tag to the reader). Uses 125 kHz EM4100 collar tags.
#define PET_TAGS { "luna", "0A00B1C2D3" }, { "milo", "0A00B1C2D4" },

// RDM6300 125 kHz reader(s): TX pin of the reader -> these ESP32 pins
#define OUTSIDE_READER_RX 16       // reader on the outside flap: pet coming in
#define INSIDE_READER_RX -1        // optional second reader inside: pet going out

// Latch: a small servo (SG90) or a 12 V solenoid through a MOSFET
#define LATCH_SERVO 1              // 1 = servo, 0 = solenoid
#define LATCH_PIN 13
#define SERVO_LOCKED_US 1000       // servo pulse widths for locked / open
#define SERVO_OPEN_US 2000
#define OPEN_SECONDS 8
