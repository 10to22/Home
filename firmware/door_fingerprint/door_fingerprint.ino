// phrasehome fingerprint sensor (stage 2 at the door)
// ESP32 + R503 capacitive fingerprint sensor (ring LED, weather-resistant). Fingers are stored
// on the sensor itself; the hub decides what a match means (it must be the same person whose
// face was just scanned) and unlocks the door.
//
// Library: "Adafruit Fingerprint Sensor Library" (Arduino Library Manager).
//
// HTTP API:
//   GET  /api/status   -> {"seq": 12, "result": "match"|"nomatch"|"", "id": 3, "confidence": 140,
//                          "enroll": "idle"|"place"|"lift"|"again"|"done"|"failed"}
//   POST /api/enroll?id=N   (finger placed twice, guided by the LED)
//   POST /api/delete?id=N
//   POST /api/led?color=red|blue|purple&mode=on|off|flash|breathe
//   POST needs X-Token (or ?token=) when TOKEN is set.

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill in your Wi-Fi and door name."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Adafruit_Fingerprint.h>

HardwareSerial fpSerial(2);
Adafruit_Fingerprint finger(&fpSerial);
WebServer server(80);

uint32_t seq = 0;
String result = "";
int lastId = -1, lastConfidence = 0;
bool waitLift = false;  // after a scan, wait for the finger to come off before the next one

enum EnrollState { IDLE, PLACE, LIFT, AGAIN, DONE, FAILED };
EnrollState enrollState = IDLE;
int enrollId = 0;
uint32_t enrollStarted = 0;
String enrollError = "";

const char *enrollName() {
  switch (enrollState) {
    case PLACE: return "place";
    case LIFT: return "lift";
    case AGAIN: return "again";
    case DONE: return "done";
    case FAILED: return "failed";
    default: return "idle";
  }
}

void led(uint8_t mode, uint8_t color, uint8_t count = 0) {
  finger.LEDcontrol(mode, 100, color, count);
}

bool authorized() {
  if (strlen(TOKEN) == 0) return true;
  if (server.header("X-Token") == TOKEN || server.arg("token") == TOKEN) return true;
  server.send(401, "application/json", "{\"error\":\"bad token\"}");
  return false;
}

String statusJson() {
  String j = "{\"door\":\"" DOOR_NAME "\",\"seq\":" + String((int)seq) + ",\"result\":\"" + result +
             "\",\"id\":" + String(lastId) + ",\"confidence\":" + String(lastConfidence) +
             ",\"enroll\":\"" + enrollName() + "\"";
  if (enrollError.length()) j += ",\"error\":\"" + enrollError + "\"";
  return j + "}";
}

void enrollFail(const char *why) {
  enrollState = FAILED;
  enrollError = why;
  led(FINGERPRINT_LED_FLASHING, FINGERPRINT_LED_RED, 3);
}

void stepEnroll() {
  if (millis() - enrollStarted > 30000) return enrollFail("timed out");
  uint8_t p = finger.getImage();
  if (enrollState == PLACE && p == FINGERPRINT_OK) {
    if (finger.image2Tz(1) != FINGERPRINT_OK) return enrollFail("couldn't read the finger");
    enrollState = LIFT;
    led(FINGERPRINT_LED_ON, FINGERPRINT_LED_PURPLE);
  } else if (enrollState == LIFT && p == FINGERPRINT_NOFINGER) {
    enrollState = AGAIN;
    led(FINGERPRINT_LED_BREATHING, FINGERPRINT_LED_PURPLE);
  } else if (enrollState == AGAIN && p == FINGERPRINT_OK) {
    if (finger.image2Tz(2) != FINGERPRINT_OK) return enrollFail("couldn't read the finger");
    if (finger.createModel() != FINGERPRINT_OK) return enrollFail("the two scans didn't match");
    if (finger.storeModel(enrollId) != FINGERPRINT_OK) return enrollFail("couldn't save");
    enrollState = DONE;
    led(FINGERPRINT_LED_FLASHING, FINGERPRINT_LED_BLUE, 3);
  }
}

void scan() {
  uint8_t p = finger.getImage();
  if (waitLift) {
    if (p == FINGERPRINT_NOFINGER) waitLift = false;
    return;
  }
  if (p != FINGERPRINT_OK) return;
  waitLift = true;
  if (finger.image2Tz() == FINGERPRINT_OK && finger.fingerSearch() == FINGERPRINT_OK) {
    result = "match";
    lastId = finger.fingerID;
    lastConfidence = finger.confidence;
  } else {
    result = "nomatch";
    lastId = -1;
    lastConfidence = 0;
  }
  seq++;  // the hub sees seq change and decides; it sets the LED to say yes or no
  Serial.printf("finger: %s id %d\n", result.c_str(), lastId);
}

void setup() {
  Serial.begin(115200);
  fpSerial.begin(57600, SERIAL_8N1, FP_RX_PIN, FP_TX_PIN);
  finger.begin(57600);
  if (!finger.verifyPassword()) {
    Serial.println("Fingerprint sensor not found - check wiring (TX/RX crossed?)");
  }
  led(FINGERPRINT_LED_GRADUAL_ON, FINGERPRINT_LED_BLUE);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) delay(300);
  String host = "phrasehome-finger-" DOOR_NAME;
  host.replace(" ", "-");
  MDNS.begin(host.c_str());
  Serial.printf("Ready: http://%s/api/status\n", WiFi.localIP().toString().c_str());

  const char *headers[] = {"X-Token"};
  server.collectHeaders(headers, 1);
  server.on("/api/status", HTTP_GET, [] { server.send(200, "application/json", statusJson()); });
  server.on("/api/enroll", HTTP_POST, [] {
    if (!authorized()) return;
    enrollId = server.arg("id").toInt();
    if (enrollId < 1 || enrollId > 200) {
      server.send(400, "application/json", "{\"error\":\"id must be 1-200\"}");
      return;
    }
    enrollState = PLACE;
    enrollError = "";
    enrollStarted = millis();
    led(FINGERPRINT_LED_BREATHING, FINGERPRINT_LED_PURPLE);
    server.send(200, "application/json", statusJson());
  });
  server.on("/api/delete", HTTP_POST, [] {
    if (!authorized()) return;
    bool ok = finger.deleteModel(server.arg("id").toInt()) == FINGERPRINT_OK;
    server.send(ok ? 200 : 400, "application/json", ok ? "{\"deleted\":true}" : "{\"error\":\"delete failed\"}");
  });
  server.on("/api/led", HTTP_POST, [] {
    if (!authorized()) return;
    String c = server.arg("color"), m = server.arg("mode");
    uint8_t color = c == "red" ? FINGERPRINT_LED_RED : c == "purple" ? FINGERPRINT_LED_PURPLE : FINGERPRINT_LED_BLUE;
    uint8_t mode = m == "off" ? FINGERPRINT_LED_OFF : m == "flash" ? FINGERPRINT_LED_FLASHING
                 : m == "breathe" ? FINGERPRINT_LED_BREATHING : FINGERPRINT_LED_ON;
    led(mode, color, m == "flash" ? 3 : 0);
    server.send(200, "application/json", "{\"ok\":true}");
  });
  server.begin();
}

void loop() {
  server.handleClient();
  if (enrollState == PLACE || enrollState == LIFT || enrollState == AGAIN) stepEnroll();
  else scan();
  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 10000) {
    lastReconnect = millis();
    WiFi.reconnect();
  }
  delay(20);
}
