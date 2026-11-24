// Optional device-node features, each switched on in config.h:
//   CLIMATE_SENSOR 4 / 3   temperature + humidity (SHT4x / SHT3x on I2C, no library needed)
//   COVER_LIST             motorized blinds / curtains on two relays (open / close)
//   IR_SEND_PIN / IR_RECV_PIN   IR blaster + learning receiver (library: IRremoteESP8266)
//   ENABLE_BLE_SCAN        report nearby Bluetooth tags (keys, wallets, pet collars)
#pragma once

// ---------------------------------------------------------------- climate
#ifdef CLIMATE_SENSOR
#include <Wire.h>
#ifndef I2C_SDA_PIN
#define I2C_SDA_PIN 32
#endif
#ifndef I2C_SCL_PIN
#define I2C_SCL_PIN 33
#endif
float climTemp = NAN, climHum = NAN;
uint32_t climLast = 0;

void climateRead() {
  const uint8_t addr = 0x44;
  Wire.beginTransmission(addr);
#if CLIMATE_SENSOR == 4
  Wire.write(0xFD);                 // SHT4x: measure, high precision
#else
  Wire.write(0x24); Wire.write(0x00);  // SHT3x: single shot, high repeatability
#endif
  if (Wire.endTransmission() != 0) return;
  delay(20);
  if (Wire.requestFrom((int)addr, 6) != 6) return;
  uint8_t b[6];
  for (int i = 0; i < 6; i++) b[i] = Wire.read();
  uint16_t t = ((uint16_t)b[0] << 8) | b[1];
  uint16_t h = ((uint16_t)b[3] << 8) | b[4];
  climTemp = -45.0f + 175.0f * t / 65535.0f;
#if CLIMATE_SENSOR == 4
  climHum = constrain(-6.0f + 125.0f * h / 65535.0f, 0.0f, 100.0f);
#else
  climHum = 100.0f * h / 65535.0f;
#endif
}
#endif

void climateStatus(String &j, bool &first) {
#ifdef CLIMATE_SENSOR
  if (isnan(climTemp)) return;
  if (!first) j += ",";
  first = false;
  j += "{\"name\":\"temperature\",\"value\":" + String(climTemp, 1) + "},";
  j += "{\"name\":\"humidity\",\"value\":" + String(climHum, 0) + "}";
#endif
}

// ---------------------------------------------------------------- covers (blinds, curtains)
struct Cover {
  const char *name;
  int pinOpen;
  int pinClose;
  float travelSeconds;  // time from fully closed to fully open
  bool activeLow;
};
#ifdef COVER_LIST
Cover covers[] = { COVER_LIST };
const int NUM_COV = sizeof(covers) / sizeof(covers[0]);
#else
Cover covers[1] = {{nullptr, -1, -1, 1, false}};
const int NUM_COV = 0;
#endif
float covPos[NUM_COV + 1];      // 0 = closed .. 100 = open (estimated from travel time)
float covStartPos[NUM_COV + 1];
int covTarget[NUM_COV + 1];
int covDir[NUM_COV + 1];        // 1 opening, -1 closing, 0 stopped
uint32_t covStart[NUM_COV + 1];

void covOut(int i, int dir) {
  Cover &c = covers[i];
  // never both relays on: switch off first
  digitalWrite(c.pinOpen, c.activeLow ? HIGH : LOW);
  digitalWrite(c.pinClose, c.activeLow ? HIGH : LOW);
  if (dir > 0) digitalWrite(c.pinOpen, c.activeLow ? LOW : HIGH);
  if (dir < 0) digitalWrite(c.pinClose, c.activeLow ? LOW : HIGH);
}

void coverStop(int i) {
  covOut(i, 0);
  covDir[i] = 0;
  prefs.putFloat((String("cv") + i).c_str(), covPos[i]);
}

void coverMove(int i, int target) {
  target = constrain(target, 0, 100);
  covOut(i, 0);
  if (abs(target - covPos[i]) < 1 && target != 0 && target != 100) {
    covDir[i] = 0;
    return;
  }
  covTarget[i] = target;
  covDir[i] = target > covPos[i] ? 1 : -1;
  if (target == covPos[i]) covDir[i] = target == 0 ? -1 : 1;  // re-run to the end stop to resync
  covStartPos[i] = covPos[i];
  covStart[i] = millis();
  delay(30);
  covOut(i, covDir[i]);
}

void coversLoop() {
  for (int i = 0; i < NUM_COV; i++) {
    if (!covDir[i]) continue;
    float elapsed = (millis() - covStart[i]) / 1000.0f;
    float pos = covStartPos[i] + covDir[i] * elapsed / covers[i].travelSeconds * 100.0f;
    covPos[i] = constrain(pos, 0.0f, 100.0f);
    bool done = covDir[i] > 0 ? pos >= covTarget[i] : pos <= covTarget[i];
    // at the very ends, run 10% longer so the motor reaches its limit switch
    if (covTarget[i] == 0 || covTarget[i] == 100)
      done = elapsed > covers[i].travelSeconds * (fabs(covTarget[i] - covStartPos[i]) / 100.0f + 0.1f);
    if (done) {
      covPos[i] = covTarget[i];
      coverStop(i);
    }
  }
}

bool coverSet() {  // handles /api/set for cover channels; returns false if it isn't one
  String name = server.arg("channel");
  for (int i = 0; i < NUM_COV; i++) {
    if (!name.equalsIgnoreCase(covers[i].name)) continue;
    if (!authorized()) return true;
    String st = server.arg("state");
    st.toLowerCase();
    if (st == "stop") coverStop(i);
    else if (server.hasArg("level")) coverMove(i, server.arg("level").toInt());
    else if (st == "on" || st == "open") coverMove(i, 100);
    else if (st == "off" || st == "close") coverMove(i, 0);
    else if (st == "toggle") coverMove(i, covPos[i] > 50 ? 0 : 100);
    server.send(200, "application/json", statusJson());
    return true;
  }
  return false;
}

void coversStatus(String &j) {
  for (int i = 0; i < NUM_COV; i++) {
    j += ",{\"name\":\"" + String(covers[i].name) + "\",\"type\":\"cover\",\"on\":" +
         (covPos[i] > 1 ? "true" : "false") + ",\"level\":" + String((int)round(covPos[i])) +
         ",\"moving\":" + (covDir[i] ? "true" : "false") + "}";
  }
}

// ---------------------------------------------------------------- IR blaster / learner
#if defined(IR_SEND_PIN) || defined(IR_RECV_PIN)
#include <IRremoteESP8266.h>
#include <IRutils.h>
#endif
#ifdef IR_SEND_PIN
#include <IRsend.h>
IRsend irsend(IR_SEND_PIN);
#endif
#ifdef IR_RECV_PIN
#include <IRrecv.h>
IRrecv irrecv(IR_RECV_PIN, 1024, 50, true);
decode_results irResults;
String irLearned = "{\"seq\":0}";
uint32_t irSeq = 0;
#endif

void irRoutes() {
#ifdef IR_SEND_PIN
  // POST /api/ir/send?protocol=NEC&code=20DF10EF&bits=32&repeat=0
  server.on("/api/ir/send", HTTP_POST, [] {
    if (!authorized()) return;
    decode_type_t t = strToDecodeType(server.arg("protocol").c_str());
    if (t == decode_type_t::UNKNOWN) {
      server.send(400, "application/json", "{\"error\":\"unknown IR protocol\"}");
      return;
    }
    String code = server.arg("code");
    if (code.startsWith("0x") || code.startsWith("0X")) code = code.substring(2);
    uint64_t value = strtoull(code.c_str(), nullptr, 16);
    uint16_t bits = server.hasArg("bits") ? server.arg("bits").toInt() : IRsend::defaultBits(t);
    uint16_t repeat = server.hasArg("repeat") ? server.arg("repeat").toInt() : 0;
    bool ok = irsend.send(t, value, bits, repeat);
    server.send(ok ? 200 : 400, "application/json", ok ? "{\"sent\":true}" : "{\"error\":\"send failed\"}");
  });
#endif
#ifdef IR_RECV_PIN
  // GET /api/ir/learn -> the last button pressed on a remote pointed at the receiver
  server.on("/api/ir/learn", HTTP_GET, [] { server.send(200, "application/json", irLearned); });
#endif
}

void irLoop() {
#ifdef IR_RECV_PIN
  if (irrecv.decode(&irResults)) {
    if (!irResults.repeat && irResults.decode_type != decode_type_t::UNKNOWN) {
      irSeq++;
      irLearned = "{\"seq\":" + String((int)irSeq) + ",\"protocol\":\"" + typeToString(irResults.decode_type) +
                  "\",\"code\":\"0x" + uint64ToString(irResults.value, 16) + "\",\"bits\":" +
                  String((int)irResults.bits) + "}";
    }
    irrecv.resume();
  }
#endif
}

// ---------------------------------------------------------------- Bluetooth tag scanning
#ifdef ENABLE_BLE_SCAN
#include <BLEDevice.h>
#include <BLEScan.h>
String bleJson = "[]";
SemaphoreHandle_t bleLock;

void bleTask(void *) {
  BLEDevice::init("");
  BLEScan *scan = BLEDevice::getScan();
  scan->setActiveScan(false);
  scan->setInterval(100);
  scan->setWindow(90);
  for (;;) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    BLEScanResults *res = scan->start(4, false);
#else
    BLEScanResults found = scan->start(4, false);
    BLEScanResults *res = &found;
#endif
    String j = "[";
    int n = 0;
    for (int i = 0; i < res->getCount() && n < 40; i++) {
      BLEAdvertisedDevice d = res->getDevice(i);
      if (d.getRSSI() < -92) continue;  // too far away to matter
      if (n++) j += ",";
      j += "{\"mac\":\"" + String(d.getAddress().toString().c_str()) + "\",\"rssi\":" + String(d.getRSSI()) + "}";
    }
    j += "]";
    xSemaphoreTake(bleLock, portMAX_DELAY);
    bleJson = j;
    xSemaphoreGive(bleLock);
    scan->clearResults();
    vTaskDelay(3000 / portTICK_PERIOD_MS);
  }
}
#endif

void bleStatus(String &j) {
#ifdef ENABLE_BLE_SCAN
  xSemaphoreTake(bleLock, portMAX_DELAY);
  j += ",\"ble\":" + bleJson;
  xSemaphoreGive(bleLock);
#endif
}

// ---------------------------------------------------------------- hooks
void extrasSetup() {
#ifdef CLIMATE_SENSOR
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  climateRead();
#endif
  for (int i = 0; i < NUM_COV; i++) {
    pinMode(covers[i].pinOpen, OUTPUT);
    pinMode(covers[i].pinClose, OUTPUT);
    covOut(i, 0);
    covPos[i] = prefs.getFloat((String("cv") + i).c_str(), 0);
  }
#ifdef IR_SEND_PIN
  irsend.begin();
#endif
#ifdef IR_RECV_PIN
  irrecv.enableIRIn();
#endif
#ifdef ENABLE_BLE_SCAN
  bleLock = xSemaphoreCreateMutex();
  xTaskCreatePinnedToCore(bleTask, "ble", 6144, nullptr, 1, nullptr, 0);
#endif
}

void extrasRoutes() { irRoutes(); }

void extrasLoop() {
#ifdef CLIMATE_SENSOR
  if (millis() - climLast > 15000) {
    climLast = millis();
    climateRead();
  }
#endif
  coversLoop();
  irLoop();
}
