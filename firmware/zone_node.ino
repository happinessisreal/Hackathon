// SCS-RG zone node firmware - ESP32, Arduino core. Wokwi Track B primary;
// the same sketch flashes to real ESP32 hardware unchanged (only the
// PER-ZONE CONFIG block below and, on real hardware, WIFI_SSID/PASSWORD
// need to change).
//
// Wire protocol implemented here must match backend/schemas.py exactly -
// sim/node.py (Phase 3) is the reference implementation of the same
// protocol and was validated end-to-end against the real backend
// (sim/driver.py, "ALL GREEN"). This sketch mirrors it field-for-field:
//   POST /api/ingest   {seq, fire, gas_norm, water_norm, occupancy, ts_device, uptime_ms}
//   GET  /api/commands/{zone_id} -> {zone_id, state, buzzer, relay, led, ts, cause}
// Both authenticated via the `X-Zone-Key` header.
//
// Rule (CLAUDE.md #1): this node sends raw/normalized sensor readings only,
// never a state or score - SAFE/WARNING/CRITICAL and the risk score are
// computed exclusively by the backend from these readings, and only the
// live server-computed state (via /api/commands) ever drives buzzer/LED/
// relay actuation.
//
// NOTE ON VALIDATION: written and cross-checked against the backend
// contract and Wokwi's official part docs, but not yet run inside the
// Wokwi simulator from this environment - needs a human smoke-test in
// Wokwi (and on real hardware, if available) before it's demo-ready.

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>

// ============================== PER-ZONE CONFIG =============================
// Edit this block for each of the 3 zones before flashing / loading into
// Wokwi. ZONE_ID and ZONE_API_KEY come from `python scripts/init_db.py`'s
// printed output (run once against your backend's DB).

#define ZONE_NAME "IoT Lab"       // for Serial logging only
#define ZONE_ID 1                 // integer zone id from init_db.py
#define ZONE_API_KEY "zk_REPLACE_ME"
#define ZONE_HAS_GAS true          // true for IoT Lab only; Server Room / Data Science Lab: false

// Wokwi-GUEST is Wokwi's built-in open network with outbound internet
// access - it only exists inside the Wokwi simulator. On real hardware,
// replace with your actual WiFi credentials.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASSWORD ""

// Wokwi's simulated network (and most real deployments) can't reach a
// laptop's `localhost` directly - point this at a public URL: an ngrok/
// cloudflared tunnel to your local backend for a demo, or a deployed
// instance. See firmware/README.md "Reaching the backend from Wokwi".
#define BACKEND_HOST "your-tunnel-or-host.example.com"
#define BACKEND_PORT 443
#define BACKEND_USE_TLS true
// ============================================================================

// Pins - see firmware/diagram.json for the Wokwi wiring (same pins apply to
// real hardware). Deliberately avoids ESP32's strapping pins (GPIO 0, 2, 5,
// 12, 15), which can interfere with boot mode selection on real hardware if
// an external component holds them high/low at power-on - Wokwi's
// simulator won't catch that, real hardware will.
const int PIN_FLAME = 33;        // digital in; HIGH = flame detected (pushbutton substitute - see diagram.json)
const int PIN_GAS_AO = 34;       // analog in, input-only ADC1 pin; only read when ZONE_HAS_GAS
const int PIN_WATER_AO = 35;     // analog in, input-only ADC1 pin (potentiometer substitute - see diagram.json)
const int PIN_PIR = 32;          // digital in
const int PIN_LED_GREEN = 26;
const int PIN_LED_YELLOW = 27;
const int PIN_LED_RED = 14;
const int PIN_BUZZER = 25;
const int PIN_RELAY = 4;

// Locked sampling interval (CLAUDE.md). Command poll is slightly faster so
// a CRITICAL transition's actuation reliably lands inside the 1s bound
// (TC5) even accounting for request latency/jitter.
const unsigned long POST_INTERVAL_MS = 750;
const unsigned long COMMAND_POLL_MS = 700;

uint32_t seqCounter = 0;
unsigned long lastPostMs = 0;
unsigned long lastCommandPollMs = 0;

// TC9b offline cache: readings that fail to POST are kept in RAM (newest
// last) and resynced in original order - with their original seq numbers -
// once connectivity returns. Mirrors sim/node.py's ZoneNode._buffer, the
// reference implementation already validated against the backend. At 750ms
// per reading, 64 slots is ~48s of outage; beyond that the oldest reading
// is dropped (documented, bounded memory beats an OOM crash mid-demo).
const int PENDING_CAPACITY = 64;
String pendingReadings[PENDING_CAPACITY];
int pendingCount = 0;

void dropOldestPending() {
  for (int i = 1; i < pendingCount; i++) {
    pendingReadings[i - 1] = pendingReadings[i];
  }
  pendingCount--;
}

void enqueueReading(const String &body) {
  if (pendingCount == PENDING_CAPACITY) {
    Serial.printf("[%s] offline buffer full, dropping oldest reading\n", ZONE_NAME);
    dropOldestPending();
  }
  pendingReadings[pendingCount++] = body;
}

WiFiClientSecure secureClient;
WiFiClient plainClient;

void connectWiFi() {
  Serial.printf("[%s] connecting to WiFi '%s'...\n", ZONE_NAME, WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.printf("\n[%s] WiFi connected, IP: %s\n", ZONE_NAME, WiFi.localIP().toString().c_str());

  if (BACKEND_USE_TLS) {
    // Simplification for a hackathon demo tunnel (e.g. ngrok, which
    // presents a valid public cert anyway): skip CA validation rather
    // than bundling a root CA store. Not a production-grade choice.
    secureClient.setInsecure();
  }
}

void syncTime() {
  // ts_device must be real wall-clock time, not "seconds since boot" - the
  // incident timeline and out-of-order/anomaly detection (TC18c) both key
  // off it. Wokwi-GUEST provides NTP reachability; real deployments need
  // an internet-connected network too.
  configTime(0, 0, "pool.ntp.org", "time.nist.gov");
  Serial.printf("[%s] waiting for NTP sync...\n", ZONE_NAME);
  time_t now = time(nullptr);
  while (now < 24 * 3600) {  // still ~1970 => not synced yet
    delay(250);
    now = time(nullptr);
  }
  Serial.printf("[%s] time synced.\n", ZONE_NAME);
}

String isoTimestamp() {
  time_t now = time(nullptr);
  struct tm timeinfo;
  gmtime_r(&now, &timeinfo);
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &timeinfo);
  return String(buf);
}

// ESP32 ADC is 12-bit (0-4095). The node's job is unit conversion (raw ADC
// -> normalized 0.0-1.0), never scoring/state - see ASSUMPTIONS.md's
// interpretation of rule 1.
float readNormalized(int pin) {
  int raw = analogRead(pin);
  return constrain(raw / 4095.0f, 0.0f, 1.0f);
}

String backendUrl(const String &path) {
  return String(BACKEND_USE_TLS ? "https://" : "http://") + BACKEND_HOST + ":" + BACKEND_PORT + path;
}

void beginRequest(HTTPClient &http, const String &url) {
  if (BACKEND_USE_TLS) {
    http.begin(secureClient, url);
  } else {
    http.begin(plainClient, url);
  }
}

bool postBody(const String &body) {
  HTTPClient http;
  beginRequest(http, backendUrl("/api/ingest"));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Zone-Key", ZONE_API_KEY);
  int code = http.POST(body);
  bool ok = code == 200;
  if (ok) {
    Serial.printf("[%s] POST /api/ingest -> %s\n", ZONE_NAME, http.getString().c_str());
  } else {
    Serial.printf("[%s] POST /api/ingest failed, code=%d (%d reading(s) buffered)\n",
                  ZONE_NAME, code, pendingCount);
  }
  http.end();
  return ok;
}

void postReading() {
  StaticJsonDocument<320> doc;
  doc["seq"] = seqCounter++;
  doc["fire"] = digitalRead(PIN_FLAME) == HIGH ? 1 : 0;
  if (ZONE_HAS_GAS) {
    doc["gas_norm"] = readNormalized(PIN_GAS_AO);
  }
  doc["water_norm"] = readNormalized(PIN_WATER_AO);
  doc["occupancy"] = digitalRead(PIN_PIR) == HIGH ? 1 : 0;
  doc["ts_device"] = isoTimestamp();
  doc["uptime_ms"] = millis();

  String body;
  serializeJson(doc, body);

  // Everything goes through the queue: newest reading is appended, then
  // the queue is flushed oldest-first. Online, that's a single POST; after
  // an outage, buffered readings resync in original order (original seq
  // numbers intact - the backend's (zone_id, seq) dedup makes any overlap
  // harmless) before the newest one goes out. (TC9b)
  enqueueReading(body);
  while (pendingCount > 0) {
    if (!postBody(pendingReadings[0])) {
      return;  // still offline - retry the same queue next cycle
    }
    dropOldestPending();
  }
}

void pollCommand() {
  HTTPClient http;
  // Arduino's String concatenation only overloads `String + X`, not
  // `const char* + String` - the literal has to be wrapped first or this
  // silently fails to compile.
  beginRequest(http, backendUrl(String("/api/commands/") + ZONE_ID));
  http.addHeader("X-Zone-Key", ZONE_API_KEY);
  int code = http.GET();
  if (code != 200) {
    Serial.printf("[%s] GET /api/commands failed, code=%d\n", ZONE_NAME, code);
    http.end();
    return;
  }

  StaticJsonDocument<320> doc;
  DeserializationError err = deserializeJson(doc, http.getString());
  http.end();
  if (err) {
    Serial.printf("[%s] command JSON parse error: %s\n", ZONE_NAME, err.c_str());
    return;
  }

  bool buzzer = doc["buzzer"];
  bool relay = doc["relay"];
  const char *led = doc["led"];

  digitalWrite(PIN_BUZZER, buzzer ? HIGH : LOW);
  digitalWrite(PIN_RELAY, relay ? HIGH : LOW);
  digitalWrite(PIN_LED_GREEN, strcmp(led, "green") == 0 ? HIGH : LOW);
  digitalWrite(PIN_LED_YELLOW, strcmp(led, "yellow") == 0 ? HIGH : LOW);
  digitalWrite(PIN_LED_RED, strcmp(led, "red") == 0 ? HIGH : LOW);
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_FLAME, INPUT_PULLDOWN);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_LED_GREEN, OUTPUT);
  pinMode(PIN_LED_YELLOW, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_RELAY, OUTPUT);
  // PIN_GAS_AO / PIN_WATER_AO are ADC1 input-only pins - no pinMode() call;
  // analogRead() works on them directly.

  connectWiFi();
  syncTime();

  digitalWrite(PIN_LED_GREEN, HIGH);  // idle default while waiting for the first command poll: SAFE
}

void loop() {
  unsigned long now = millis();

  if (now - lastPostMs >= POST_INTERVAL_MS) {
    lastPostMs = now;
    postReading();
  }

  if (now - lastCommandPollMs >= COMMAND_POLL_MS) {
    lastCommandPollMs = now;
    pollCommand();
  }
}
