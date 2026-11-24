// Copy this file to config.h (same folder) and fill it in.
#pragma once

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"
#define NODE_NAME "garage"           // add it to the hub's esp nodes: garage: phrasehome-garage.local

// HC-SR04P (3.3 V version) distance sensor on the wall in front of the car's bumper
#define TRIG_PIN 5
#define ECHO_PIN 18

// LEDs (or a 3-light traffic-light module), through ~220 ohm resistors
#define GREEN_PIN 25
#define YELLOW_PIN 26
#define RED_PIN 27

#define STOP_CM 40          // red: stop here
#define SLOW_CM 110         // yellow: getting close
#define CAR_CM 200          // closer than this = a car is in the garage
