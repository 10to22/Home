// phrasehome device node
// ESP32 that switches relays and dims LED strips when the phrasehome hub tells it to.
// Remembers its state across power cuts. Has a web page at http://phrasehome-<name>.local
//
// HTTP API:
//   GET  /api/status                                  -> JSON of all channels and inputs
//   POST /api/set?channel=lights&state=on|off|toggle  (and/or &level=0-100 for dimmable)
//        &for=5  -> switch back after 5 seconds (pumps, feeders, garage-door button, door strike)
//   header X-Token: <TOKEN> if a token is configured
// Inputs (sensors) are read here and reported in /api/status; the hub polls them.
// Optional extras (extras.h): temperature/humidity, blinds/curtains, IR blaster, Bluetooth tags.
//
// Works with the Arduino ESP32 core 2.x and 3.x. No extra libraries needed.

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>
#include <Preferences.h>

struct Channel {
  const char *name;
  int pin;
  bool dimmable;
  bool activeLow;
  int button;
};

enum InputType { DIGITAL = 0, ANALOG = 1, ANALOG_INVERTED = 2 };
struct Input {
  const char *name;
  int pin;
  int type;
  bool activeLow;
};

#if __has_include("config.h")
#include "config.h"
#else
#error "Copy config.example.h to config.h and fill in your Wi-Fi and channels."
#endif

Channel channels[] = { CHANNEL_LIST };
const int NUM = sizeof(channels) / sizeof(channels[0]);

#ifndef INPUT_LIST
#define INPUT_LIST
#endif
Input inputs[] = { INPUT_LIST { nullptr, -1, DIGITAL, false } };  // last entry marks the end
const int NUM_IN = sizeof(inputs) / sizeof(inputs[0]) - 1;
bool inOn[NUM_IN + 1];
uint32_t inCount[NUM_IN + 1];
uint32_t inChanged[NUM_IN + 1];
int inValue[NUM_IN + 1];
uint32_t offAt[NUM];  // millis() at which a timed "for=" switch flips back, 0 = none
bool isOn[NUM];
int level[NUM];            // 0-100, dimmable channels only
bool lastButton[NUM];
uint32_t lastButtonChange[NUM];

WebServer server(80);
Preferences prefs;
String statusJson();
bool authorized();
void climateStatus(String &j, bool &first);
void coversStatus(String &j);
void bleStatus(String &j);
bool coverSet();
void extrasSetup();
void extrasRoutes();
void extrasLoop();

const int PWM_FREQ = 5000;
const int PWM_BITS = 10;

void applyOutput(int i) {
  Channel &c = channels[i];
  if (c.dimmable) {
    int duty = isOn[i] ? (level[i] * ((1 << PWM_BITS) - 1)) / 100 : 0;
    if (c.activeLow) duty = ((1 << PWM_BITS) - 1) - duty;
#if ESP_ARDUINO_VERSION_MAJOR >= 3
    ledcWrite(c.pin, duty);
#else
    ledcWrite(i, duty);
#endif
  } else {
    digitalWrite(c.pin, (isOn[i] != c.activeLow) ? HIGH : LOW);
  }
}

void save(int i) {
  String k = String("c") + i;
  prefs.putBool((k + "on").c_str(), isOn[i]);
  prefs.putInt((k + "lv").c_str(), level[i]);
}

int findChannel(const String &name) {
  for (int i = 0; i < NUM; i++)
    if (name.equalsIgnoreCase(channels[i].name)) return i;
  return -1;
}

String statusJson() {
  String j = "{\"node\":\"" NODE_NAME "\",\"channels\":[";
  for (int i = 0; i < NUM; i++) {
    if (i) j += ",";
    j += "{\"name\":\"" + String(channels[i].name) + "\",\"on\":" + (isOn[i] ? "true" : "false");
    if (channels[i].dimmable) j += ",\"level\":" + String(level[i]);
    j += "}";
  }
  coversStatus(j);
  j += "],\"inputs\":[";
  bool first = NUM_IN == 0;
  for (int i = 0; i < NUM_IN; i++) {
    if (i) j += ",";
    j += "{\"name\":\"" + String(inputs[i].name) + "\"";
    if (inputs[i].type == DIGITAL)
      j += ",\"on\":" + String(inOn[i] ? "true" : "false") + ",\"count\":" + String((int)inCount[i]);
    else
      j += ",\"value\":" + String(inValue[i]);
    j += "}";
  }
  climateStatus(j, first);
  j += "]";
  bleStatus(j);
  return j + "}";
}

bool authorized() {
  if (strlen(TOKEN) == 0) return true;
  if (server.header("X-Token") == TOKEN || server.arg("token") == TOKEN) return true;
  server.send(401, "application/json", "{\"error\":\"bad token\"}");
  return false;
}

void handleSet() {
  if (coverSet()) return;  // blinds/curtains (extras.h)
  if (!authorized()) return;
  int i = findChannel(server.arg("channel"));
  if (i < 0) {
    server.send(404, "application/json", "{\"error\":\"no such channel\"}");
    return;
  }
  String st = server.arg("state");
  st.toLowerCase();
  if (st == "on") isOn[i] = true;
  else if (st == "off") isOn[i] = false;
  else if (st == "toggle") isOn[i] = !isOn[i];
  else if (st.length()) {
    server.send(400, "application/json", "{\"error\":\"state must be on, off or toggle\"}");
    return;
  }
  if (server.hasArg("level") && channels[i].dimmable) {
    level[i] = constrain((int)server.arg("level").toInt(), 0, 100);
    if (!st.length()) isOn[i] = level[i] > 0;  // "set lamp to 40%" also turns it on
  }
  applyOutput(i);
  save(i);
  offAt[i] = 0;
  if (server.hasArg("for")) {
    float secs = server.arg("for").toFloat();
    if (secs > 0) offAt[i] = millis() + (uint32_t)(secs * 1000);
  }
  Serial.printf("%s -> %s %d%%\n", channels[i].name, isOn[i] ? "on" : "off", level[i]);
  server.send(200, "application/json", statusJson());
}

const char PAGE[] PROGMEM = R"HTML(<!doctype html><meta name=viewport content="width=device-width">
<title>phrasehome</title><style>body{font-family:sans-serif;max-width:420px;margin:2em auto}
.c{display:flex;justify-content:space-between;align-items:center;padding:.6em 0;border-bottom:1px solid #ddd}
button{font-size:1em;padding:.4em 1em}</style><h2 id=t>phrasehome</h2><div id=l></div>
<script>
const tok=new URLSearchParams(location.search).get('token')||'';
async function go(q){const r=await fetch('/api/set?'+q+'&token='+encodeURIComponent(tok),{method:'POST'});if(r.ok)draw(await r.json())}
function draw(s){t.textContent=s.node;l.innerHTML=s.channels.map(c=>`<div class=c><b>${c.name}</b>
${c.level!==undefined?`<input type=range min=0 max=100 value=${c.level} onchange="go('channel=${c.name}&level='+this.value)">`:''}
<button onclick="go('channel=${c.name}&state=toggle')">${c.on?'ON':'off'}</button></div>`).join('')}
fetch('/api/status').then(r=>r.json()).then(draw)
</script>)HTML";

void setup() {
  Serial.begin(115200);
  prefs.begin("phrasehome", false);
  for (int i = 0; i < NUM; i++) {
    Channel &c = channels[i];
    String k = String("c") + i;
    isOn[i] = prefs.getBool((k + "on").c_str(), false);
    level[i] = prefs.getInt((k + "lv").c_str(), 100);
    if (c.dimmable) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
      ledcAttach(c.pin, PWM_FREQ, PWM_BITS);
#else
      ledcSetup(i, PWM_FREQ, PWM_BITS);
      ledcAttachPin(c.pin, i);
#endif
    } else {
      pinMode(c.pin, OUTPUT);
    }
    applyOutput(i);
    if (c.button >= 0) {
      pinMode(c.button, INPUT_PULLUP);
      lastButton[i] = digitalRead(c.button);
    }
  }

  for (int i = 0; i < NUM_IN; i++) {
    if (inputs[i].type == DIGITAL) {
      pinMode(inputs[i].pin, inputs[i].activeLow ? INPUT_PULLUP : INPUT);
      inOn[i] = (digitalRead(inputs[i].pin) == LOW) == inputs[i].activeLow;
    }
  }

  extrasSetup();

  WiFi.mode(WIFI_STA);
  WiFi.setHostname("phrasehome-" NODE_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
    Serial.print(".");
  }
  Serial.printf("\nIP %s  ->  http://phrasehome-%s.local\n", WiFi.localIP().toString().c_str(), NODE_NAME);
  String mdnsName = "phrasehome-" NODE_NAME;
  mdnsName.replace(" ", "-");
  MDNS.begin(mdnsName.c_str());
  MDNS.addService("http", "tcp", 80);

  const char *headers[] = {"X-Token"};
  server.collectHeaders(headers, 1);
  server.on("/", HTTP_GET, [] { server.send_P(200, "text/html", PAGE); });
  server.on("/api/status", HTTP_GET, [] { server.send(200, "application/json", statusJson()); });
  server.on("/api/set", HTTP_POST, handleSet);
  extrasRoutes();
  server.begin();
}

void loop() {
  server.handleClient();
  extrasLoop();
  static uint32_t lastReconnect = 0;
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnect > 10000) {
    lastReconnect = millis();
    WiFi.reconnect();
  }

  // Timed switches ("for=") flip back when their time is up.
  for (int i = 0; i < NUM; i++) {
    if (offAt[i] && (int32_t)(millis() - offAt[i]) >= 0) {
      offAt[i] = 0;
      isOn[i] = !isOn[i];
      applyOutput(i);
      save(i);
    }
  }

  // Sensors: debounce digital inputs (counting presses so none are missed), smooth analog ones.
  for (int i = 0; i < NUM_IN; i++) {
    if (inputs[i].type == DIGITAL) {
      bool on = (digitalRead(inputs[i].pin) == LOW) == inputs[i].activeLow;
      if (on != inOn[i] && millis() - inChanged[i] > 30) {
        inChanged[i] = millis();
        inOn[i] = on;
        if (on) inCount[i]++;
      }
    } else {
      int pct = analogRead(inputs[i].pin) * 100 / 4095;
      if (inputs[i].type == ANALOG_INVERTED) pct = 100 - pct;
      inValue[i] = (inValue[i] * 7 + pct) / 8;
    }
  }

  // Physical buttons (to GND): toggle on press.
  for (int i = 0; i < NUM; i++) {
    if (channels[i].button < 0) continue;
    bool b = digitalRead(channels[i].button);
    if (b != lastButton[i] && millis() - lastButtonChange[i] > 50) {
      lastButtonChange[i] = millis();
      lastButton[i] = b;
      if (b == LOW) {
        isOn[i] = !isOn[i];
        applyOutput(i);
        save(i);
      }
    }
  }
  delay(2);
}

#include "extras.h"
