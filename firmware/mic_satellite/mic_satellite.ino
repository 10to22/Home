// phrasehome mic satellite
// ESP32 + INMP441 I2S microphone. Streams audio to the phrasehome hub, which does all
// the speech recognition. The LED blinks once when a command is heard, stays lit briefly
// when it worked, and flashes 3 times if it failed.
//
// Works with the Arduino ESP32 core 2.x and 3.x. No extra libraries needed.

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill in your Wi-Fi and room."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>

#if ESP_ARDUINO_VERSION_MAJOR >= 3
#include <ESP_I2S.h>
I2SClass mic;
#else
#include <driver/i2s.h>
#endif

static const int SAMPLE_RATE = 16000;
static const int CHUNK = 512;  // samples per UDP packet (32 ms)

WiFiUDP udp;
IPAddress hubIp;
int32_t raw[CHUNK];
uint8_t packet[5 + 32 + CHUNK * 2];
size_t headerLen = 0;
uint32_t lastResolve = 0;
int32_t dcOffset = 0;

// ---------- LED feedback ----------
volatile int ledPattern = 0;  // 0 none, 1 heard, 2 ok, 3 failed
uint32_t ledStart = 0;

void led(bool on) {
  if (LED_PIN >= 0) digitalWrite(LED_PIN, on ? HIGH : LOW);
}

void updateLed() {
  if (!ledPattern) return;
  uint32_t t = millis() - ledStart;
  switch (ledPattern) {
    case 1: led(t < 120); if (t > 120) ledPattern = 0; break;
    case 2: led(t < 800); if (t > 800) ledPattern = 0; break;
    case 3: led((t / 120) % 2 == 0 && t < 720); if (t > 720) ledPattern = 0; break;
  }
  if (!ledPattern) led(false);
}

// ---------- microphone ----------
void micBegin() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  mic.setPins(I2S_SCK_PIN, I2S_WS_PIN, -1, I2S_SD_PIN);
  if (!mic.begin(I2S_MODE_STD, SAMPLE_RATE, I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO,
                 MIC_USE_RIGHT_CHANNEL ? I2S_STD_SLOT_RIGHT : I2S_STD_SLOT_LEFT)) {
    Serial.println("I2S init failed - check wiring/pins");
  }
#else
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX);
  cfg.sample_rate = SAMPLE_RATE;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  cfg.channel_format = MIC_USE_RIGHT_CHANNEL ? I2S_CHANNEL_FMT_ONLY_RIGHT : I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 256;
  cfg.use_apll = false;
  i2s_driver_install(I2S_NUM_0, &cfg, 0, NULL);

  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = I2S_SCK_PIN;
  pins.ws_io_num = I2S_WS_PIN;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = I2S_SD_PIN;
  i2s_set_pin(I2S_NUM_0, &pins);
#endif
}

size_t micRead(int32_t *buf, size_t bytes) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  return mic.readBytes((char *)buf, bytes);
#else
  size_t got = 0;
  i2s_read(I2S_NUM_0, buf, bytes, &got, portMAX_DELAY);
  return got;
#endif
}

// ---------- network ----------
void connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // keeps audio smooth
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.printf("\nWi-Fi OK, IP %s\n", WiFi.localIP().toString().c_str());
}

bool resolveHub() {
  String host = HUB_HOST;
  if (hubIp.fromString(host)) return true;
  if (host.endsWith(".local")) {
    IPAddress ip = MDNS.queryHost(host.substring(0, host.length() - 6));
    if (ip != IPAddress(0, 0, 0, 0)) { hubIp = ip; return true; }
  } else if (WiFi.hostByName(host.c_str(), hubIp)) {
    return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  if (LED_PIN >= 0) pinMode(LED_PIN, OUTPUT);
  led(true);
  connectWifi();
  String mdnsName = String("phrasehome-mic-") + ROOM;
  mdnsName.replace(" ", "-");
  MDNS.begin(mdnsName.c_str());
  while (!resolveHub()) {
    Serial.printf("Can't find hub %s, retrying...\n", HUB_HOST);
    delay(2000);
  }
  Serial.printf("Hub: %s:%d, room: %s\n", hubIp.toString().c_str(), HUB_PORT, ROOM);
  udp.begin(HUB_PORT);

  // Header: "PHM1" + room length + room
  const char *room = ROOM;
  size_t n = strlen(room);
  if (n > 32) n = 32;
  memcpy(packet, "PHM1", 4);
  packet[4] = (uint8_t)n;
  memcpy(packet + 5, room, n);
  headerLen = 5 + n;

  micBegin();
  led(false);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }
  // Re-resolve a .local hub now and then in case its IP changed.
  if (millis() - lastResolve > 60000) {
    lastResolve = millis();
    resolveHub();
  }

  size_t got = micRead(raw, sizeof(raw));
  size_t samples = got / sizeof(int32_t);
  int16_t *out = (int16_t *)(packet + headerLen);
  for (size_t i = 0; i < samples; i++) {
    int32_t s = raw[i] >> GAIN_SHIFT;
    dcOffset += (s - dcOffset) >> 10;  // remove the mic's DC offset
    s -= dcOffset;
    if (s > 32767) s = 32767;
    if (s < -32768) s = -32768;
    out[i] = (int16_t)s;
  }
  if (samples) {
    udp.beginPacket(hubIp, HUB_PORT);
    udp.write(packet, headerLen + samples * 2);
    udp.endPacket();
  }

  // Feedback from the hub: "PHA1" + status
  int len = udp.parsePacket();
  if (len >= 5) {
    uint8_t ack[8];
    udp.read(ack, sizeof(ack));
    if (memcmp(ack, "PHA1", 4) == 0 && ack[4] >= 1 && ack[4] <= 3) {
      ledPattern = ack[4];
      ledStart = millis();
    }
  } else if (len > 0) {
    udp.flush();
  }
  updateLed();
}
