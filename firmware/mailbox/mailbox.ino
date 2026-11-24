// phrasehome mailbox sensor
// Battery-powered ESP32 (deep sleep, months on an 18650). Wakes when the mailbox lid opens,
// tells the hub ("you've got mail"), then sleeps again. Also wakes once a day to report its
// battery, so you hear about it before it runs flat.
//
// Hub side: an automation on `external.mailbox` (see config.smarthome.example.yaml).

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill it in."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <esp_sleep.h>

RTC_DATA_ATTR uint32_t wakes = 0;

int batteryPercent() {
  if (BATTERY_PIN < 0) return -1;
  uint32_t mv = 0;
  for (int i = 0; i < 8; i++) mv += analogReadMilliVolts(BATTERY_PIN);
  float v = mv / 8.0f * 2 / 1000.0f;               // 1:2 divider
  return constrain((int)((v - 3.3f) / (4.15f - 3.3f) * 100), 0, 100);  // Li-ion 3.3 V .. 4.15 V
}

bool post(const char *kind, int battery) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  String url = String("http://") + HUB_HOST + ":" + HUB_PORT + "/api/event";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  if (strlen(HUB_TOKEN)) http.addHeader("X-Token", HUB_TOKEN);
  String body = String("{\"kind\":\"") + kind + "\"" + (battery >= 0 ? ",\"battery\":" + String(battery) : "") + "}";
  int code = http.POST(body);
  http.end();
  WiFi.disconnect(true);
  return code == 200;
}

void setup() {
  wakes++;
  esp_sleep_wakeup_cause_t why = esp_sleep_get_wakeup_cause();
  pinMode(LID_PIN, INPUT_PULLUP);
  int battery = batteryPercent();
  if (why == ESP_SLEEP_WAKEUP_EXT0) {
    post("mailbox", battery);
    // wait for the lid to close again (up to a minute) so we don't wake straight back up
    for (int i = 0; i < 600 && digitalRead(LID_PIN) == LID_OPEN_LEVEL; i++) delay(100);
  } else if (why == ESP_SLEEP_WAKEUP_TIMER) {
    post("mailbox_battery", battery);
  } else {
    post("mailbox_online", battery);  // first power-up
  }
  esp_sleep_enable_ext0_wakeup((gpio_num_t)LID_PIN, LID_OPEN_LEVEL);
  esp_sleep_enable_timer_wakeup(24ULL * 3600 * 1000000);  // daily battery report
  esp_deep_sleep_start();
}

void loop() {}
