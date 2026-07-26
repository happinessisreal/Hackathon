# firmware/ - ESP32 zone node (Wokwi Track B)

One sketch (`zone_node.ino`) runs all three zones identically - only the
`PER-ZONE CONFIG` block at the top changes per zone. `diagram.json` wires up
**IoT Lab** (the richest zone: fire + gas + water + PIR); Server Room and
Data Science Lab use the same sketch and the same diagram minus the gas
sensor (`gas1` part + its 3 connections removed, `ZONE_HAS_GAS` set to
`false`).

**Validation status**: written and cross-checked field-for-field against
the backend contract (`backend/schemas.py`) and Phase 3's `sim/node.py`
(the same protocol, already proven "ALL GREEN" against the real backend),
and against Wokwi's official part docs for every component used. It has
**not** yet been run inside the Wokwi simulator from this environment -
needs a human smoke-test in Wokwi (open the project, hit play, watch
Serial output and the LEDs) before it's demo-ready. Flag anything that
doesn't compile or behave as described here.

## Wiring substitutions (Wokwi has no dedicated part for these)

| Zone sensor | Wokwi part used | Why |
|---|---|---|
| Flame sensor | `wokwi-pushbutton` ("FLAME (sim)") | Wokwi has no flame/IR sensor part. Wired to 3V3 through `INPUT_PULLDOWN` on the ESP32 side: unpressed = LOW (no flame), pressed = HIGH (flame) - click-and-hold in the Wokwi UI to simulate sustained flame (needed to clear the 5-reading debounce), release to test the flicker/decay cases. |
| Water level sensor | `wokwi-potentiometer` | Wokwi has no water-level part. Drag the knob to raise/lower the simulated water_norm reading, same as a real analog water-level sensor's voltage divider. |
| MQ-2 gas sensor | `wokwi-gas-sensor` (native part, IoT Lab only) | Wokwi does model this one - it's an actual MQ-2 simulation. |
| PIR | `wokwi-pir-motion-sensor` (native part) | Real part, click to trigger motion. |

Every substitution is visually labeled in the diagram and reads through
`analogRead()`/`digitalRead()` exactly like the real sensor it stands in
for - the backend can't tell the difference, and the same pins/wiring
apply if you swap in real components on physical hardware.

## Pin map

| Signal | GPIO | Notes |
|---|---|---|
| Flame (digital in) | 33 | `INPUT_PULLDOWN` |
| Gas AO (analog in) | 34 | input-only ADC1 pin; IoT Lab only |
| Water SIG (analog in) | 35 | input-only ADC1 pin |
| PIR OUT (digital in) | 32 | |
| LED green | 26 | via 220ohm resistor |
| LED yellow | 27 | via 220ohm resistor |
| LED red | 14 | via 220ohm resistor |
| Buzzer | 25 | |
| Relay IN | 4 | |

Deliberately avoids ESP32's strapping pins (GPIO 0, 2, 5, 12, 15) for every
output - an external component holding one of those high/low at power-on
can put the chip into the wrong boot mode. Wokwi's simulator won't catch
this; real hardware will (usually as a board that won't boot after you
wire something up). All pins above are safe on both real ESP32 DevKit V1
boards and in the simulator.

## Per-zone config

Edit the top of `zone_node.ino` before flashing/loading each zone:

```cpp
#define ZONE_NAME      "IoT Lab"          // Server Room | Data Science Lab
#define ZONE_ID        1                  // from `python scripts/init_db.py` output
#define ZONE_API_KEY   "zk_..."           // from `python scripts/init_db.py` output
#define ZONE_HAS_GAS   true                // false for Server Room / Data Science Lab
```

`ZONE_ID`/`ZONE_API_KEY` come from running `python scripts/init_db.py`
against your backend's DB (it prints all three zones' keys once, and is
idempotent to re-run).

## Reaching the backend from Wokwi

**Wokwi's simulated network (`Wokwi-GUEST`) can reach the public internet,
but it cannot reach `localhost`/`127.0.0.1` on your own machine.** If the
backend is running locally (the normal dev setup - see the root
`README.md`), Wokwi has no route to it. Options, cheapest first:

1. **Tunnel** (recommended for the demo video): run `ngrok http 8000` (or
   `cloudflared tunnel --url http://localhost:8000`) alongside the backend,
   and set `BACKEND_HOST`/`BACKEND_PORT`/`BACKEND_USE_TLS` in the sketch to
   the tunnel's public host.

   **The sketch ships with `BACKEND_USE_TLS false` / `BACKEND_PORT 80`.**
   Measured behaviour against a `cloudflared` quick tunnel: the emulated
   ESP32's mbedTLS handshake never completes - `HTTPClient` returns `-1`
   (`HTTPC_ERROR_CONNECTION_REFUSED`) and *nothing* reaches the backend,
   while the identical request to port 80 on the same tunnel hostname
   returns 200. The tunnel edge still terminates TLS for the dashboard and
   every other client; only the simulated node's own leg is plaintext.
   That's an acceptable trade for a simulator demo and is called out here
   rather than hidden. The TLS path is still implemented
   (`WiFiClientSecure::setInsecure()`, which skips CA validation - fine for
   a demo tunnel with a real cert, not a production choice): set
   `BACKEND_USE_TLS true` + `BACKEND_PORT 443` on real hardware, where the
   handshake works normally.
2. **Deploy the backend** somewhere public for the duration of the demo and
   point `BACKEND_HOST` at it directly.

Either way, set `BACKEND_HOST` before recording - it's the one thing that
can't be discovered by reading the code, only by trying to connect.

## Flashing to real hardware

The sketch is unmodified for real hardware except `WIFI_SSID`/
`WIFI_PASSWORD` (real credentials instead of `Wokwi-GUEST`) and
`BACKEND_HOST` (same tunnel/deploy consideration as above, unless the
board is on the same LAN as the backend). Board: "DOIT ESP32 DEVKIT V1" in
the Arduino IDE board manager (fqbn `esp32:esp32:esp32doit-devkit-v1`).
Libraries: `ArduinoJson` 6.21.5 (see `libraries.txt`; install via Arduino
Library Manager for a real-hardware build since Wokwi's auto-install only
applies inside the simulator).
