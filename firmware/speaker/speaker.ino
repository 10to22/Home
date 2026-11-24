// phrasehome speaker module
// ESP32 + I2S amplifier (MAX98357A) or DAC (PCM5102A). Two modes:
//   bluetooth - a normal Bluetooth speaker: pair your phone with "phrasehome <name>"
//   group     - plays the phrasehome hub's music in sync with the other speakers in the group
// Switch with a voice command, `phrasehome speakers group|bluetooth`, the web API, or by holding
// the BOOT button for 1.5 s. The mode is saved, and the speaker restarts to switch (~3 s).
//
// Needs a *classic* ESP32 (ESP32-WROOM / DevKit). The S3, C3 and C6 have no Bluetooth audio.
// Libraries (see firmware/README.md): ESP32-A2DP and arduino-audio-tools, both by pschatzmann.
// Tools -> Partition Scheme -> "Huge APP (3MB No OTA)", because Bluetooth + Wi-Fi is big.
//
// HTTP API:
//   GET  /api/status                       -> {"name","mode","volume","synced","bt_connected"}
//   POST /api/mode?mode=bluetooth|group
//   POST /api/volume?level=0-100   or  ?change=+10 / -10
//   header X-Token: <TOKEN> if a token is configured

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill in your Wi-Fi and speaker name."
#endif

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <lwip/sockets.h>
#include <esp_timer.h>
#include "AudioTools.h"
#include "BluetoothA2DPSink.h"

enum Mode { MODE_BLUETOOTH = 0, MODE_GROUP = 1 };

static const int RATE = 44100;
static const int FRAMES = 256;                  // stereo frames per network packet (5.8 ms)
static const int PCM_BYTES = FRAMES * 2 * 2;
static const int HDR = 16;                      // "PHS1" + u32 seq + i64 play_at
static const int SLOTS = 96;                    // jitter buffer: ~560 ms
static const int AUDIO_PORT = 7779;
static const int TIME_PORT = 7778;
static const int DMA_COUNT = 8, DMA_SIZE = 512;
// Audio sitting in the I2S DMA buffers (bytes / 4 bytes per frame)
static const int64_t I2S_LATENCY_US = (int64_t)DMA_COUNT * DMA_SIZE / 4 * 1000000 / RATE;

I2SStream i2s;
BluetoothA2DPSink *a2dp = nullptr;
WebServer server(80);
Preferences prefs;
Mode mode;
volatile int volume;  // 0-100

// ---------- group-mode state ----------
struct Slot {
  volatile bool valid;
  uint32_t seq;
  int64_t playAt;
  int16_t pcm[FRAMES * 2];
};
Slot *slots = nullptr;
int sock = -1;
volatile uint32_t hubAddr = 0;      // learned from the first audio packet
volatile int64_t clockOffset = 0;   // hub clock - our clock, microseconds
volatile bool synced = false;
int64_t offSamples[16], rttSamples[16];
int nSamples = 0, sampleIdx = 0, syncSent = 0;

int64_t hubNow() { return esp_timer_get_time() + clockOffset; }

void led(bool on) { if (LED_PIN >= 0) digitalWrite(LED_PIN, on ? HIGH : LOW); }

// ---------- HTTP ----------
bool authorized() {
  if (strlen(TOKEN) == 0) return true;
  if (server.header("X-Token") == TOKEN || server.arg("token") == TOKEN) return true;
  server.send(401, "application/json", "{\"error\":\"bad token\"}");
  return false;
}

String statusJson() {
  String j = "{\"name\":\"" SPEAKER_NAME "\",\"mode\":\"";
  j += mode == MODE_GROUP ? "group" : "bluetooth";
  j += "\",\"volume\":" + String((int)volume);
  j += ",\"synced\":" + String(synced ? "true" : "false");
  j += ",\"bt_connected\":" + String(a2dp && a2dp->is_connected() ? "true" : "false");
  return j + "}";
}

void applyVolume() {
  if (a2dp) a2dp->set_volume((uint8_t)(volume * 127 / 100));
}

void setMode(Mode m) {
  prefs.putInt("mode", m);
  Serial.printf("switching to %s mode, restarting\n", m == MODE_GROUP ? "group" : "bluetooth");
  delay(300);
  ESP.restart();
}

void handleMode() {
  if (!authorized()) return;
  String m = server.arg("mode");
  Mode want;
  if (m == "group") want = MODE_GROUP;
  else if (m == "bluetooth") want = MODE_BLUETOOTH;
  else { server.send(400, "application/json", "{\"error\":\"mode must be bluetooth or group\"}"); return; }
  server.send(200, "application/json", "{\"mode\":\"" + m + "\",\"restarting\":" +
              (want != mode ? "true" : "false") + "}");
  if (want != mode) setMode(want);
}

void handleVolume() {
  if (!authorized()) return;
  int v = volume;
  if (server.hasArg("level")) v = server.arg("level").toInt();
  else if (server.hasArg("change")) v += server.arg("change").toInt();
  volume = constrain(v, 0, MAX_VOLUME);
  prefs.putInt("volume", volume);
  applyVolume();
  server.send(200, "application/json", statusJson());
}

// ---------- group mode: network receive (core 0) ----------
void sendTimeRequest() {
  if (!hubAddr) return;
  uint8_t req[12];
  uint64_t t0 = (uint64_t)esp_timer_get_time();
  memcpy(req, "PHT1", 4);
  memcpy(req + 4, &t0, 8);
  sockaddr_in to = {};
  to.sin_family = AF_INET;
  to.sin_port = htons(TIME_PORT);
  to.sin_addr.s_addr = hubAddr;
  sendto(sock, req, sizeof(req), 0, (sockaddr *)&to, sizeof(to));
  syncSent++;
}

void onTimeReply(const uint8_t *buf) {
  uint64_t t0;
  int64_t hub;
  memcpy(&t0, buf + 4, 8);
  memcpy(&hub, buf + 12, 8);
  int64_t t1 = esp_timer_get_time();
  int64_t rtt = t1 - (int64_t)t0;
  if (rtt < 0 || rtt > 200000) return;
  offSamples[sampleIdx] = hub - ((int64_t)t0 + t1) / 2;
  rttSamples[sampleIdx] = rtt;
  sampleIdx = (sampleIdx + 1) % 16;
  if (nSamples < 16) nSamples++;
  int best = 0;  // the reply that came back fastest is the most accurate
  for (int i = 1; i < nSamples; i++)
    if (rttSamples[i] < rttSamples[best]) best = i;
  clockOffset = offSamples[best];
  if (nSamples >= 4) synced = true;
}

void rxTask(void *) {
  static uint8_t buf[HDR + PCM_BYTES + 16];
  uint32_t allowed = 0;
  if (strlen(HUB_IP)) allowed = inet_addr(HUB_IP);
  for (;;) {
    sockaddr_in from;
    socklen_t fl = sizeof(from);
    int n = recvfrom(sock, buf, sizeof(buf), 0, (sockaddr *)&from, &fl);
    if (n <= 0) continue;
    if (allowed && from.sin_addr.s_addr != allowed) continue;
    if (n >= 20 && memcmp(buf, "PHT1", 4) == 0) {
      onTimeReply(buf);
    } else if (n == HDR + PCM_BYTES && memcmp(buf, "PHS1", 4) == 0) {
      if (hubAddr != from.sin_addr.s_addr) {
        hubAddr = from.sin_addr.s_addr;
        syncSent = 0;
      }
      uint32_t seq;
      int64_t at;
      memcpy(&seq, buf + 4, 4);
      memcpy(&at, buf + 8, 8);
      Slot &s = slots[seq % SLOTS];
      s.valid = false;
      s.seq = seq;
      s.playAt = at;
      memcpy(s.pcm, buf + HDR, PCM_BYTES);
      s.valid = true;
    }
  }
}

// ---------- group mode: synced playback (core 1) ----------
int16_t silence[FRAMES * 2];
int16_t out[(FRAMES + 8) * 2];

void writeFrames(const int16_t *pcm, int frames) {
  i2s.write((const uint8_t *)pcm, frames * 4);
}

void playTask(void *) {
  int64_t expect = -1;
  int misses = 0;
  const int64_t FRAME_US = 1000000 / RATE;
  for (;;) {
    if (!synced) { writeFrames(silence, FRAMES); expect = -1; continue; }

    if (expect < 0) {  // (re)start: earliest packet that's still in the future
      int64_t now = hubNow() + I2S_LATENCY_US;
      int64_t bestAt = INT64_MAX;
      for (int i = 0; i < SLOTS; i++)
        if (slots[i].valid && slots[i].playAt > now && slots[i].playAt < bestAt) {
          bestAt = slots[i].playAt;
          expect = slots[i].seq;
        }
      if (expect < 0) { writeFrames(silence, FRAMES); led(false); continue; }
      misses = 0;
    }

    Slot &s = slots[expect % SLOTS];
    if (!s.valid || s.seq != (uint32_t)expect) {  // lost packet, or the music stopped
      if (++misses > 30) expect = -1;
      else { writeFrames(silence, FRAMES); expect++; }
      continue;
    }
    misses = 0;
    led(true);

    // How far off are we? > 0 means we're late. Correct big errors at once, small ones gently.
    int64_t err = hubNow() + I2S_LATENCY_US - s.playAt;
    int start = 0, pad = 0;
    if (err < -5000) {  // early: wait with exact-length silence
      int f = min((int)(-err / FRAME_US), FRAMES);
      writeFrames(silence, f);
      continue;
    } else if (err > 5000) {  // late: drop the overdue part
      int f = (int)(err / FRAME_US);
      if (f >= FRAMES) { s.valid = false; expect++; continue; }
      start = f;
    } else if (err > 1000) {
      start = 2;  // drift: drop 2 frames
    } else if (err < -1000) {
      pad = 2;    // drift: repeat 2 frames
    }

    // volume (squared for a natural-feeling curve), then play
    int32_t gain = (int32_t)volume * volume;  // 0..10000
    int n = 0;
    for (int p = 0; p < pad; p++, n++) {
      out[n * 2] = (int16_t)(s.pcm[0] * gain / 10000);
      out[n * 2 + 1] = (int16_t)(s.pcm[1] * gain / 10000);
    }
    for (int f = start; f < FRAMES; f++, n++) {
      out[n * 2] = (int16_t)(s.pcm[f * 2] * gain / 10000);
      out[n * 2 + 1] = (int16_t)(s.pcm[f * 2 + 1] * gain / 10000);
    }
    s.valid = false;
    expect++;
    writeFrames(out, n);
  }
}

// ---------- setup ----------
void connectWifi(uint32_t timeoutMs) {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  uint32_t t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < timeoutMs) {
    led((millis() / 250) % 2);
    delay(100);
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("Wi-Fi OK, IP %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("Wi-Fi not connected (Bluetooth still works)");
}

void setup() {
  Serial.begin(115200);
  if (LED_PIN >= 0) pinMode(LED_PIN, OUTPUT);
  if (MODE_BUTTON_PIN >= 0) pinMode(MODE_BUTTON_PIN, INPUT_PULLUP);
  prefs.begin("phrasehome", false);
  mode = (Mode)prefs.getInt("mode", MODE_BLUETOOTH);
  volume = constrain(prefs.getInt("volume", 60), 0, MAX_VOLUME);
  Serial.printf("phrasehome speaker '%s' in %s mode\n", SPEAKER_NAME,
                mode == MODE_GROUP ? "group" : "bluetooth");

  auto cfg = i2s.defaultConfig(TX_MODE);
  cfg.pin_bck = I2S_BCLK_PIN;
  cfg.pin_ws = I2S_LRC_PIN;
  cfg.pin_data = I2S_DOUT_PIN;
  cfg.sample_rate = RATE;
  cfg.channels = 2;
  cfg.bits_per_sample = 16;
  cfg.buffer_count = DMA_COUNT;
  cfg.buffer_size = DMA_SIZE;
  cfg.use_apll = true;  // accurate 44.1 kHz clock, needed for staying in sync
  i2s.begin(cfg);

  if (mode == MODE_GROUP) {
    connectWifi(30000);
    WiFi.setSleep(false);  // smooth streaming (only allowed when Bluetooth is off)
    slots = (Slot *)calloc(SLOTS, sizeof(Slot));
    sock = socket(AF_INET, SOCK_DGRAM, 0);
    sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(AUDIO_PORT);
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    bind(sock, (sockaddr *)&addr, sizeof(addr));
    xTaskCreatePinnedToCore(rxTask, "rx", 4096, nullptr, 5, nullptr, 0);
    xTaskCreatePinnedToCore(playTask, "play", 4096, nullptr, 10, nullptr, 1);
  } else {
    // Wi-Fi stays on (with power saving, which Bluetooth needs) so the hub can still control us.
    connectWifi(15000);
    a2dp = new BluetoothA2DPSink(i2s);
    a2dp->start("phrasehome " SPEAKER_NAME);
    applyVolume();
  }

  String host = "phrasehome-speaker-" SPEAKER_NAME;
  host.replace(" ", "-");
  MDNS.begin(host.c_str());
  MDNS.addService("http", "tcp", 80);
  const char *headers[] = {"X-Token"};
  server.collectHeaders(headers, 1);
  server.on("/api/status", HTTP_GET, [] { server.send(200, "application/json", statusJson()); });
  server.on("/api/mode", HTTP_POST, handleMode);
  server.on("/api/volume", HTTP_POST, handleVolume);
  server.begin();
}

void loop() {
  server.handleClient();

  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 10000) {
    lastReconnect = millis();
    WiFi.reconnect();
  }

  // Clock sync with the hub: quickly at first, then once a second.
  static uint32_t lastSync = 0;
  if (mode == MODE_GROUP && millis() - lastSync > (syncSent < 10 ? 50u : 1000u)) {
    lastSync = millis();
    sendTimeRequest();
  }

  // LED in Bluetooth mode: solid when a phone is connected, slow blink when waiting.
  if (mode == MODE_BLUETOOTH) led(a2dp->is_connected() || (millis() / 1000) % 2);

  // Hold the button 1.5 s to switch modes.
  static uint32_t pressedAt = 0;
  if (MODE_BUTTON_PIN >= 0) {
    if (digitalRead(MODE_BUTTON_PIN) == LOW) {
      if (!pressedAt) pressedAt = millis();
      else if (millis() - pressedAt > 1500) setMode(mode == MODE_GROUP ? MODE_BLUETOOTH : MODE_GROUP);
    } else {
      pressedAt = 0;
    }
  }
  delay(2);
}
