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
// VALIDATION STATUS: smoke-tested in the Wokwi simulator against the live
// backend over a tunnel. Two runs of 29 and 43 minutes produced 1396 accepted
// readings and four correct sensor-driven transitions
// (SAFE->WARNING->SAFE at risk 39.2 / 52.4 / 53.6 / 52.7), confirming the
// flame debounce, the linear decay on removal, and the fusion weights
// end-to-end. Not yet run on physical ESP32 hardware.
//
// Two things that testing exposed and that are fixed below: the mbedTLS
// handshake does not complete inside the simulator (see BACKEND_USE_TLS),
// and seqCounter must not restart at 0 on reboot (see seqCounter).

#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <time.h>

// ============================== PER-ZONE CONFIG =============================
// All three zones run this identical sketch - only ZONE_SELECT changes.
// Change exactly ONE line below, and pair it with the matching diagram:
//
//   ZONE_SELECT 1  -> IoT Lab           firmware/diagram.json                  (has gas)
//   ZONE_SELECT 2  -> Server Room       firmware/diagram_server_room.json      (no gas)
//   ZONE_SELECT 3  -> Data Science Lab  firmware/diagram_data_science_lab.json (no gas)
//
// A single selector rather than four hand-edited defines: getting ZONE_ID and
// ZONE_API_KEY out of step points a node at the wrong zone's key and every
// POST 401s, which looks exactly like a dead network.
//
// ZONE_ID / ZONE_API_KEY values come from `python scripts/init_db.py`'s printed
// output. They are per-database - re-running init_db.py against a fresh DB
// mints new keys, and these must then be updated.

#define ZONE_SELECT 1

#if ZONE_SELECT == 1
  #define ZONE_NAME "IoT Lab"
  #define ZONE_ID 1
  #define ZONE_API_KEY "zk_R7Kou88PZiNlkjNVbdJQUYagE7CJFJEo"
  #define ZONE_HAS_GAS true       // only zone with an MQ-2
#elif ZONE_SELECT == 2
  #define ZONE_NAME "Server Room"
  #define ZONE_ID 2
  #define ZONE_API_KEY "zk_lgi3kTP-SGFVOJ8604W8-zWfLBNWxBW5"
  #define ZONE_HAS_GAS false
#elif ZONE_SELECT == 3
  #define ZONE_NAME "Data Science Lab"
  #define ZONE_ID 3
  #define ZONE_API_KEY "zk_EU5XNNgUmppachYLp0EmSxOOrl-bDtYH"
  #define ZONE_HAS_GAS false
#else
  #error "ZONE_SELECT must be 1 (IoT Lab), 2 (Server Room) or 3 (Data Science Lab)"
#endif

// Wokwi-GUEST is Wokwi's built-in open network with outbound internet
// access - it only exists inside the Wokwi simulator. On real hardware,
// replace with your actual WiFi credentials.
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASSWORD ""

// Wokwi's simulated network (and most real deployments) can't reach a
// laptop's `localhost` directly - point this at a public URL: an ngrok/
// cloudflared tunnel to your local backend for a demo, or a deployed
// instance. See firmware/README.md "Reaching the backend from Wokwi".
// Plain HTTP for the Wokwi demo: the emulated ESP32's mbedTLS handshake
// against the tunnel's edge does not complete (HTTPClient returns -1 before
// any request reaches the backend), while port 80 through the same tunnel
// serves fine. The tunnel still terminates TLS for every other client; only
// the simulated node's leg is plaintext, which is acceptable for a demo and
// avoids a handshake that the simulator can't reliably drive. Set TLS back
// to true + port 443 for real hardware.
#define BACKEND_HOST "instrumentation-lakes-captain-cas.trycloudflare.com"
#define BACKEND_PORT 80
#define BACKEND_USE_TLS false
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

// Locked sampling interval (CLAUDE.md).
//
// COMMAND_POLL_MS must be substantially LARGER than one loop cycle, not just
// larger than POST_INTERVAL_MS. Both postReading() and pollCommand() block on
// a full HTTP round-trip, and inside Wokwi's browser-proxied gateway a single
// request costs ~1.4s. Measured, over two real Wokwi runs:
//
//   COMMAND_POLL_MS = 700   -> median gap 2.96s, 42% of gaps > 3.0s
//   COMMAND_POLL_MS = 2500  -> median gap 2.80s, 33% of gaps > 3.0s
//
// 2500 barely helped because the cycle itself is ~2.8s, so the poll still
// came due almost every iteration. Anything above OFFLINE_AFTER_SECONDS
// (3.0s) makes the zone card flicker OFFLINE while it is in fact healthy.
// At 8000 the poll fires roughly every 4th-6th cycle, leaving most gaps at
// the cost of a single POST (~1.4s) with real margin under the cutoff.
//
// The trade is LED/buzzer actuation lag of up to ~8s, which is acceptable
// because the tunnel had already put the 1s TC5 bound out of reach; TC5
// timing is demonstrated via sim/driver.py against localhost (3ms RTT,
// no tunnel in the path). On real hardware on the same LAN, set this to 700.
const unsigned long POST_INTERVAL_MS = 750;
const unsigned long COMMAND_POLL_MS = 8000;

// Seeded from wall-clock time in setup() AFTER syncTime(), never left at 0.
// readings has UNIQUE(zone_id, seq) and the backend short-circuits a repeated
// seq as a duplicate without applying it to zone state, so a node that
// restarts its seq at 0 silently re-sends seqs the DB already holds: every
// POST returns 200 {"duplicate": true} while the zone never updates. The node
// only becomes visible again after climbing past the previous run's max seq -
// ~33 minutes at Wokwi's ~1.4s/reading after a 1400-reading run. Epoch
// seconds are monotonic across reboots and advance faster than seq does
// (1 seq per ~1.4s vs 1 per second), so successive boots can never collide.
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

// Cap on buffered readings flushed per loop() pass. Draining all 64 in one
// pass blocks the loop for ~90s at Wokwi's ~1.4s/request: no command polls
// (so LEDs freeze on their last state) and no new samples for a minute and a
// half. Flushing a few per pass keeps the backlog draining oldest-first -
// order matters for the TC9b resync story - while the loop stays alive.
const int MAX_FLUSH_PER_CYCLE = 4;

// Rate limit on reconnect attempts so a down link never turns loop() into a
// blocking retry spin.
const unsigned long WIFI_RETRY_MS = 5000;
unsigned long lastWifiAttemptMs = 0;

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

// Non-blocking, rate-limited link recovery. connectWiFi() runs once in
// setup(), so without this a single dropped link is terminal: every POST
// fails forever, the buffer saturates at 64 and starts discarding the oldest
// reading, and only a reboot recovers. That also breaks the *resync* half of
// TC9b - the RAM cache correctly survives a backend outage (link up, server
// unreachable), but a Wi-Fi outage could never reconnect to replay it. Rare
// inside Wokwi; the common failure mode on real hardware.
bool wifiUp() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }
  unsigned long now = millis();
  if (now - lastWifiAttemptMs >= WIFI_RETRY_MS) {
    lastWifiAttemptMs = now;
    Serial.printf("[%s] WiFi down (status=%d), reconnecting...\n", ZONE_NAME, WiFi.status());
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
  return false;
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

  // Sample-and-cache always; only attempt the network when the link is up.
  // Without this guard each doomed POST still pays a full connection timeout
  // before failing, so an outage would stall sampling as well as delivery.
  if (!wifiUp()) {
    return;
  }

  int flushed = 0;
  while (pendingCount > 0 && flushed < MAX_FLUSH_PER_CYCLE) {
    if (!postBody(pendingReadings[0])) {
      return;  // still offline - retry the same queue next cycle
    }
    dropOldestPending();
    flushed++;
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

  // 512, not 320: deserializing from a String makes ArduinoJson copy keys as
  // well as values, and this response carries seven members including a ~28
  // char timestamp - ~200 bytes used, too little headroom. An overflow returns
  // NoMemory, which the check below turns into a silent "stop actuating while
  // the LEDs hold their last state" - the exact failure that is hardest to
  // spot on camera.
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, http.getString());
  http.end();
  if (err) {
    Serial.printf("[%s] command JSON parse error: %s\n", ZONE_NAME, err.c_str());
    return;
  }

  bool buzzer = doc["buzzer"];
  bool relay = doc["relay"];
  // `| ""` supplies a default: a bare `doc["led"]` yields nullptr when the key
  // is missing, and strcmp() on nullptr is undefined behaviour - in practice
  // an ESP32 panic and reboot, which would restart setup() mid-take.
  const char *led = doc["led"] | "";

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
  // Echo the resolved backend URL once: if a request later fails, this is
  // what distinguishes "wrong/stale host compiled in" from a genuine
  // connectivity failure.
  Serial.printf("[%s] backend: %s\n", ZONE_NAME, backendUrl("/api/ingest").c_str());
  syncTime();

  // Must come after syncTime() - time(nullptr) is meaningless before NTP.
  seqCounter = (uint32_t)time(nullptr);
  Serial.printf("[%s] seq base: %u\n", ZONE_NAME, seqCounter);

  digitalWrite(PIN_LED_GREEN, HIGH);  // idle default while waiting for the first command poll: SAFE
}

void loop() {
  unsigned long now = millis();

  if (now - lastPostMs >= POST_INTERVAL_MS) {
    lastPostMs = now;
    postReading();  // samples and caches even with the link down (TC9b)
  }

  // Re-read millis(): postReading() blocks on HTTP for ~1.4s per request, so
  // the value captured above is stale by the time we get here.
  if (millis() - lastCommandPollMs >= COMMAND_POLL_MS && WiFi.status() == WL_CONNECTED) {
    lastCommandPollMs = millis();
    pollCommand();
  }
}
