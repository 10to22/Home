// phrasehome pet door
// RFID collar tag reader (RDM6300, 125 kHz EM4100 tags) + latch (servo or solenoid).
// Only your pets' tags unlock the flap, for a few seconds. With a second reader inside, it
// knows which way they went ("Luna came in"). The hub logs each use: petdoor.used {pet, direction}.
//
// HTTP: GET /api/status -> {"seq": 5, "pet": "luna", "allowed": true, "direction": "in", "mode": "auto"}
//       POST /api/mode?mode=auto|locked|open   (locked: nobody, e.g. vet day; open: always open)

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill it in."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

struct Pet { const char *name; const char *tag; };
Pet pets[] = { PET_TAGS { nullptr, nullptr } };

WebServer server(80);
HardwareSerial outside(1);
#if INSIDE_READER_RX >= 0
HardwareSerial inside(2);
#endif
uint32_t seq = 0;
String lastPet = "", lastDir = "", mode = "auto";
bool lastAllowed = false;
uint32_t openUntil = 0;

void latch(bool open) {
#if LATCH_SERVO
  uint32_t duty = (uint32_t)((open ? SERVO_OPEN_US : SERVO_LOCKED_US) / 20000.0 * 65535);
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(LATCH_PIN, duty);
#else
  ledcWrite(0, duty);
#endif
#else
  digitalWrite(LATCH_PIN, open ? HIGH : LOW);
#endif
}

// RDM6300 frame: 0x02, 10 hex chars (tag), 2 hex chars (checksum), 0x03
String readTag(HardwareSerial &s) {
  static char buf[2][14];
  static int len[2] = {0, 0};
  int k = (&s == &outside) ? 0 : 1;
  while (s.available()) {
    char c = s.read();
    if (c == 0x02) { len[k] = 0; continue; }
    if (c == 0x03) {
      if (len[k] == 12) {
        uint8_t sum = 0;
        for (int i = 0; i < 10; i += 2) sum ^= strtol(String(buf[k]).substring(i, i + 2).c_str(), nullptr, 16);
        if (sum == strtol(String(buf[k]).substring(10, 12).c_str(), nullptr, 16))
          return String(buf[k]).substring(0, 10);
      }
      len[k] = 0;
      continue;
    }
    if (len[k] < 12) { buf[k][len[k]++] = c; buf[k][len[k]] = 0; }
  }
  return "";
}

void seen(const String &tag, const char *direction) {
  static String lastTag;
  static uint32_t lastAt = 0;
  if (tag == lastTag && millis() - lastAt < 5000) return;  // the same visit
  lastTag = tag;
  lastAt = millis();
  const char *name = nullptr;
  for (int i = 0; pets[i].name; i++)
    if (tag.equalsIgnoreCase(pets[i].tag)) name = pets[i].name;
  Serial.printf("tag %s (%s) %s\n", tag.c_str(), name ? name : "unknown", direction);
  lastPet = name ? name : "unknown";
  lastAllowed = name && mode != "locked";
  lastDir = direction;
  seq++;
  if (lastAllowed) {
    latch(true);
    openUntil = millis() + OPEN_SECONDS * 1000;
  }
}

void setup() {
  Serial.begin(115200);
  outside.begin(9600, SERIAL_8N1, OUTSIDE_READER_RX, -1);
#if INSIDE_READER_RX >= 0
  inside.begin(9600, SERIAL_8N1, INSIDE_READER_RX, -1);
#endif
#if LATCH_SERVO
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttach(LATCH_PIN, 50, 16);
#else
  ledcSetup(0, 50, 16);
  ledcAttachPin(LATCH_PIN, 0);
#endif
#else
  pinMode(LATCH_PIN, OUTPUT);
#endif
  latch(false);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  String host = "phrasehome-" DOOR_NAME;
  host.replace(" ", "-");
  MDNS.begin(host.c_str());
  server.on("/api/status", HTTP_GET, [] {
    server.send(200, "application/json", "{\"seq\":" + String((int)seq) + ",\"pet\":\"" + lastPet +
                "\",\"allowed\":" + (lastAllowed ? "true" : "false") + ",\"direction\":\"" + lastDir +
                "\",\"mode\":\"" + mode + "\"}");
  });
  server.on("/api/mode", HTTP_POST, [] {
    if (strlen(TOKEN) && server.arg("token") != TOKEN && server.header("X-Token") != TOKEN) {
      server.send(401, "application/json", "{\"error\":\"bad token\"}");
      return;
    }
    String m = server.arg("mode");
    if (m == "auto" || m == "locked" || m == "open") mode = m;
    latch(mode == "open");
    server.send(200, "application/json", "{\"mode\":\"" + mode + "\"}");
  });
  const char *headers[] = {"X-Token"};
  server.collectHeaders(headers, 1);
  server.begin();
}

void loop() {
  server.handleClient();
  String t = readTag(outside);
  if (t.length()) seen(t, "in");
#if INSIDE_READER_RX >= 0
  t = readTag(inside);
  if (t.length()) seen(t, "out");
#endif
  if (openUntil && (int32_t)(millis() - openUntil) >= 0 && mode != "open") {
    openUntil = 0;
    latch(false);
  }
}
