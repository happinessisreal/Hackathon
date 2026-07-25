# SCS-RG - Multi-Hazard Smart Campus Safety & Response Grid

RoboFusion 1.0 Techathon, Round 1. Team: `[TEAM_NAME]`.

Track B (Wokwi ESP32) primary; `sim/` Python simulators are used for load and
protocol-level edge-case testing only (duplicate seq, malformed payloads,
concurrent races) - not as a substitute for the Wokwi hardware demo.

## Status

- **Phase 0** (scaffold, schema, seed): done.
- **Phase 1** (ingestion, fusion, state machine, incidents, ack, auth/RBAC, tests): done.
- **Phase 2** (WS broadcast + full dashboard): done.
- **Phase 3** (sim/ zone simulators + driver scenarios + resilience): done.
- **Phase 4** (firmware): written, cross-checked against the backend
  contract, **not yet Wokwi-smoke-tested by a human** - see
  `firmware/README.md`.
- **Phase 5** (bonuses): **all four attempted.** Bonus 1 (camera occupancy
  cross-check - real frame-difference detection on a webcam/video standing
  in for the ESP32-CAM on Track B, PIR cross-check feeding the priority
  ranking only), Bonus 2 (short-term risk trend - sparkline + "trending
  toward CRITICAL" chip), Bonus 3 (ML risk prediction - logistic
  regression on clearly-stated synthetic data, metrics reported, pure-
  Python serving, code-level no-actuation guard), Bonus 4 (NL incident
  reporting - DeepSeek LLM call with an offline keyword-parser fallback,
  both gated by the same deterministic validation, feeding a decaying
  advisory term into the priority queue only).
- **Phase 6** (docs/video/submission): `DOCUMENTATION.md`, `VIDEO_SCRIPT.md`,
  and `SUBMISSION.md` written. **Video not yet recorded** - blocked on the
  Phase 4 Wokwi smoke-test above; see `SUBMISSION.md` for the full
  checklist of what's left.

See `ASSUMPTIONS.md` for defaults chosen without pausing for confirmation,
`DOCUMENTATION.md` for the full architecture/API/schema writeup,
`VIDEO_SCRIPT.md` for the shot-by-shot recording plan, and `SUBMISSION.md`
for the submission checklist.

## Setup (< 10 commands)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env        # (or `cp` on macOS/Linux) - defaults work as-is
python scripts/init_db.py     # creates schema + seeds 3 zones, staff1/admin1
uvicorn backend.main:app --reload --port 8000
```

`scripts/init_db.py` prints each zone's `X-Zone-Key` (for firmware/Wokwi) and
the demo login credentials (`staff1`/`staff123`, `admin1`/`admin123`). It's
idempotent - re-run any time without duplicating rows.

Open http://localhost:8000/ for the dashboard (login with the credentials
printed above) or http://localhost:8000/api/ping to confirm just the backend
is up.

## Tests

```bash
pytest -q
```

98 tests covering: fusion formula math, flame debounce/decay, PIR hold,
CRITICAL hysteresis (entry/exit/min-hold/flip-flood suppression), duplicate
seq dedup, out-of-order/anomaly flagging, incident open/resolve/re-trigger
+ hazard-type labeling, concurrent ack race (exactly-once via DB unique
constraint), restart recovery (rebuilds state from DB, not SAFE), WS
snapshot/broadcast, 401/403/404/409/422 auth/RBAC/validation paths, Bonus 2
trend/slope math, Bonus 4 NL-report parsing (fallback keyword parser,
LLM-path validation gate, priority-queue advisory boost and its decay/cap),
Bonus 3 prediction (sigmoid math, artifact honesty, and a source-scan test
that fails if prediction is ever imported near actuation), and Bonus 1
camera cross-check (freshness, PIR agreement/disagreement, priority-queue
rescue, risk-formula isolation). Every automated pass has also been
re-verified live: a real uvicorn process + real WebSocket client (no
TestClient), and the dashboard driven end-to-end in an actual browser.

## Simulator / scenario driver

```bash
python scripts/init_db.py                    # if not already seeded
uvicorn backend.main:app --port 8000 &        # backend must be running

python sim/driver.py                          # run every scenario group (tc1-tc7, tc18, tc22, tc23)
python sim/driver.py tc1 tc22                 # run just these groups
python sim/driver.py --phantom 30             # TC11a load test (30 concurrent fake zones)
python sim/driver.py --base-url http://127.0.0.1:8001   # target a different server

python sim/seed.py --readings 10000 --incidents 300      # TC19 perf seed

# Bonus 1 camera node (frame-difference occupancy, cross-checked vs PIR):
python sim/camera_node.py --zone "IoT Lab" --webcam      # needs opencv-python
python sim/camera_node.py --zone "IoT Lab" --video footage.mp4
python sim/camera_node.py --zone "IoT Lab" --synthetic   # no OpenCV needed

# Bonus 3 model retrain (ml/model.json ships in the repo; backend serves it
# in pure Python - sklearn is only needed to re-train):
python ml/train.py
```

Every scenario prints narration cues (`[tc1a] ...`) intended to be read aloud
over the video footage, and a PASS/FAIL line per observable check. All
scenarios are idempotent/re-runnable - safe to re-run for a retake. Formula
math, hysteresis, dedup, ack races, and restart recovery are proven in
`tests/`; the driver proves the same behavior holds over real HTTP against a
live, running system.

**Don't point the driver at a server/dashboard you're actively watching for
a demo** - `tc22`/`tc23`/etc. flip real zone states, and `--phantom` creates
and later deletes zone rows. Run it against a disposable DB/port
(`DATABASE_URL=... uvicorn ... --port 8801`, `sim/driver.py --base-url
http://127.0.0.1:8801`) unless you're deliberately recording it live.

## Firmware (Wokwi Track B)

`firmware/zone_node.ino` - one ESP32 sketch, all 3 zones (per-zone config
block at the top). `firmware/diagram.json` wires up IoT Lab (all 4 sensor
types); Server Room / Data Science Lab use the same sketch and diagram
minus the gas sensor. See `firmware/README.md` for per-zone config, the
Wokwi part substitutions (no native flame/water-level parts - pushbutton
and potentiometer stand in, clearly labeled), the pin map, and - important
before recording anything - how to reach a locally-running backend from
Wokwi's simulated network (it can't reach `localhost`; needs a tunnel).

**Not yet validated in the actual Wokwi simulator or on real hardware** -
written and cross-checked against the backend contract and Wokwi's part
docs, but needs a human smoke-test before it's demo-ready.

**Wokwi project link(s)**: `[TODO - add the saved/shared Wokwi project
URL(s) here once the smoke-test in firmware/README.md is done]`.

## Bonus 4 setup (NL incident reporting)

Works offline out of the box (regex/keyword fallback parser, no key
required). To use the real LLM path, put a DeepSeek (or any OpenAI-
compatible `chat/completions` provider) key in `.env`:

```bash
LLM_API_KEY=sk-...
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

`.env` is gitignored - never commit a real key. If the key is missing, or
the call errors/times out, `/api/report` degrades to the offline parser
automatically and the response's `understood.source` field says which path
was actually used (`"llm"` or `"fallback"`) - check this before recording
the Bonus 4 segment of the video (see `VIDEO_SCRIPT.md`'s pre-recording
checklist).

## API

See the locked table in `CLAUDE.md` for the authoritative list; full
request/response example payloads for every endpoint are in
`DOCUMENTATION.md` §7.

## Backup

```bash
scripts/backup.sh   # sqlite3 .backup, safe to run live (WAL mode)
```
