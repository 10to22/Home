// phrasehome door camera
// AI-Thinker ESP32-CAM (OV2640). Serves a still photo at /capture for the hub's door scanner.
// The hub does all the face recognition. The camera only takes pictures.
//
// Board: "AI Thinker ESP32-CAM". Flash it with a USB-serial adapter or an ESP32-CAM-MB board.
//
// HTTP API:
//   GET  /capture[?token=]        -> JPEG
//   GET  /status                  -> {"door": "...", "light": false}
//   POST /light?on=1|0[&token=]

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill in your Wi-Fi and door name."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include "esp_camera.h"

// AI-Thinker ESP32-CAM pin map
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM   0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM     5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

WebServer server(80);
bool lightOn = false;

bool authorized() {
  if (strlen(TOKEN) == 0 || server.arg("token") == TOKEN) return true;
  server.send(401, "text/plain", "bad token");
  return false;
}

void setLight(bool on) {
  lightOn = on;
  if (LIGHT_PIN >= 0) digitalWrite(LIGHT_PIN, on ? HIGH : LOW);
}

void handleCapture() {
  if (!authorized()) return;
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) {
    server.send(503, "text/plain", "camera busy");
    return;
  }
#if AUTO_LIGHT
  // Very dark pictures compress to tiny JPEGs: switch the light on for the next shots.
  setLight(fb->len < 6000);
#endif
  server.sendHeader("Cache-Control", "no-store");
  server.setContentLength(fb->len);
  server.send(200, "image/jpeg", "");
  server.client().write(fb->buf, fb->len);
  esp_camera_fb_return(fb);
}

void setup() {
  Serial.begin(115200);
  if (LIGHT_PIN >= 0) pinMode(LIGHT_PIN, OUTPUT);
  setLight(false);

  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAME_SIZE;
  c.jpeg_quality = JPEG_QUALITY;
  c.fb_count = psramFound() ? 2 : 1;
  c.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;  // always the newest picture, never a stale one
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed (0x%x). Check the ribbon cable. Restarting...\n", err);
    delay(3000);
    ESP.restart();
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  String host = "phrasehome-doorcam-" DOOR_NAME;
  host.replace(" ", "-");
  MDNS.begin(host.c_str());
  MDNS.addService("http", "tcp", 80);
  Serial.printf("\nReady: http://%s/capture  (http://%s.local/capture)\n",
                WiFi.localIP().toString().c_str(), host.c_str());

  server.on("/capture", HTTP_GET, handleCapture);
  server.on("/status", HTTP_GET, [] {
    server.send(200, "application/json",
                String("{\"door\":\"" DOOR_NAME "\",\"light\":") + (lightOn ? "true" : "false") + "}");
  });
  server.on("/light", HTTP_POST, [] {
    if (!authorized()) return;
    setLight(server.arg("on") == "1");
    server.send(200, "application/json", String("{\"light\":") + (lightOn ? "true" : "false") + "}");
  });
  server.begin();
}

void loop() {
  server.handleClient();
  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 10000) {
    lastReconnect = millis();
    WiFi.reconnect();
  }
  delay(2);
}
