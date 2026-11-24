// phrasehome parking assistant
// Ultrasonic distance sensor + traffic-light LEDs: green while you pull in, yellow as you get
// close, red to stop (flashing red if you're too close). The lights go off once you're parked.
// It also tells the hub whether the car is home (input "car") so automations can react,
// e.g. close the garage door, or say "welcome home".
//
// HTTP: GET /api/status -> {"node":..., "inputs":[{"name":"car","on":true,"count":12},
//                                                {"name":"distance","value":38}]}

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill it in."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

WebServer server(80);
int distanceCm = -1;
bool carHere = false;
uint32_t arrivals = 0;
uint32_t stillSince = 0;
int lastDistance = -1;

int ping() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long us = pulseIn(ECHO_PIN, HIGH, 30000);
  return us ? (int)(us / 58) : 999;
}

int measure() {  // median of 3 pings
  int a = ping(); delay(15);
  int b = ping(); delay(15);
  int c = ping();
  return max(min(a, b), min(max(a, b), c));
}

void lights(bool g, bool y, bool r) {
  digitalWrite(GREEN_PIN, g);
  digitalWrite(YELLOW_PIN, y);
  digitalWrite(RED_PIN, r);
}

void setup() {
  Serial.begin(115200);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(YELLOW_PIN, OUTPUT);
  pinMode(RED_PIN, OUTPUT);
  lights(true, true, true);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  String host = "phrasehome-" NODE_NAME;
  host.replace(" ", "-");
  MDNS.begin(host.c_str());
  server.on("/api/status", HTTP_GET, [] {
    server.send(200, "application/json",
                String("{\"node\":\"" NODE_NAME "\",\"channels\":[],\"inputs\":[{\"name\":\"car\",\"on\":") +
                (carHere ? "true" : "false") + ",\"count\":" + String((int)arrivals) +
                "},{\"name\":\"distance\",\"value\":" + String(distanceCm) + "}]}");
  });
  server.begin();
  lights(false, false, false);
}

void loop() {
  server.handleClient();
  static uint32_t last = 0;
  if (millis() - last < 150) return;
  last = millis();
  distanceCm = measure();

  bool present = distanceCm < CAR_CM;
  if (present && !carHere) arrivals++;
  carHere = present;

  // moving? (distance changing by more than 3 cm)
  if (lastDistance < 0 || abs(distanceCm - lastDistance) > 3) stillSince = millis();
  lastDistance = distanceCm;
  bool parked = present && millis() - stillSince > 20000;  // still for 20 s: done, lights off

  if (!present || parked) lights(false, false, false);
  else if (distanceCm < STOP_CM - 15) lights(false, false, (millis() / 250) % 2);  // too close: flash
  else if (distanceCm < STOP_CM) lights(false, false, true);
  else if (distanceCm < SLOW_CM) lights(false, true, false);
  else lights(true, false, false);

  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 10000) {
    lastReconnect = millis();
    WiFi.reconnect();
  }
}
