# CLAUDE.md — RoboFusion 1.0 Round 1: SCS-RG (Multi-Hazard Smart Campus Safety & Response Grid)

## Mission

Build the complete SCS-RG system for the RoboFusion 1.0 Techathon Round 1. Rubric = 200 marks (160 core + 40 bonus). **Hard deadline: 27 July 2026, 12:00 AM BST.** Every decision optimizes marks-per-hour. The 7-minute video demo is the real deliverable — every feature must be *demonstrable on screen with narration*. If a feature works but can't be shown convincingly in the video, it earns zero.

Team name: `Error404`.

## Non-negotiable architecture rules (rubric traps — violating any of these loses marks across multiple test cases)

1. **Risk scores are computed ONLY on the backend** from raw sensor values. Zone nodes send raw numbers, never a state or score. (TC6)
2. **Single source of truth**: dashboard always renders backend/DB state. No client-side state derivation that can drift. (TC25)
3. **Exactly one acknowledgment per incident**, enforced by a DB unique constraint, not application logic alone. (TC7b)
4. **Predicted/ML/NL-derived values NEVER trigger actuation** (relay/buzzer). Only the live server-computed risk score can. (Bonus 3e, Bonus 4)
5. **OFFLINE ≠ SAFE.** A stale/disconnected zone or sensor shows OFFLINE distinctly. (TC4d, TC23a)
6. **On restart, backend rebuilds zone states from the DB** before accepting connections — never assumes SAFE. (TC9a, TC23e)
7. All inputs validated: malformed/out-of-range/impossible values rejected with clear errors, never silently absorbed. (TC6b, TC23f)

## Stack (locked — do not relitigate)

- **Backend**: Python 3.11+, FastAPI, uvicorn, `websockets` via FastAPI WS. Async throughout.
- **DB**: SQLite via SQLAlchemy, WAL mode enabled. All constraints in schema (FKs with `ON DELETE RESTRICT`, unique constraints, indexes). Backup = `sqlite3 backup` script (rubric explicitly accepts this).
- **Frontend**: single-page vanilla HTML/CSS/JS (ES modules), served as static files by FastAPI. No build step. WebSocket client with reconnect + full-state catch-up.
- **Zone nodes**: two implementations of the same protocol:
  - `firmware/zone_node.ino` — ESP32 sketch for **Wokwi** (Track B compliance): reads flame (digital), MQ-2 (analog), water level (analog), PIR (digital); drives buzzer, G/Y/R LEDs, relay; POSTs raw readings over HTTP every 750 ms; polls `/api/commands/{zone}` for actuation commands. Same sketch flashes to real hardware if any is available.
  - `sim/` — Python zone simulators + scenario driver (the video engine, load generator, and edge-case injector).
- **Track declaration**: Track B (Wokwi) primary. README states clearly which zones are Wokwi-simulated and that the Python sim is used for load/phantom testing only.

## Zones (implement these 3)

| Zone | Sensors | Why |
|---|---|---|
| IoT Lab | fire + gas + water + PIR | richest zone |
| Server Room | fire + water + PIR | high stakes, low occupancy — flood-equivalent AC leak |
| Data Science Lab | fire + water + PIR | second flood profile, moderate occupancy |

All three run identical node code; backend treats all zones identically.

## Risk fusion formula (locked — document justification verbatim in docs)

```
risk_score(zone) =
    40 * fire_signal        (0 or 1, after debounce)
  + 25 * gas_level_norm     (0.0–1.0, normalized to MQ-2 datasheet range)
  + 25 * water_level_norm   (0.0–1.0)
  + 10 * occupancy_factor   (0 or 1 from PIR)

SAFE < 30   |   30 ≤ WARNING < 65   |   CRITICAL ≥ 65
Hysteresis: exit CRITICAL only below 55, with min 3 s hold.
```

Justification (use this reasoning, not the case doc's verbatim numbers): fire weighted highest — fastest-escalating, most destructive in electronics labs. Water raised to parity with gas because two of our three zones are server/GPU rooms where a condensate leak is the *realistic* catastrophic hazard. Occupancy lowest in a zone's own score — an empty zone with a real fire is still an emergency for equipment and responders — but occupancy is weighted heavily in **inter-zone priority ranking**, where it belongs (life > assets).

**Sensor pipeline params (locked):**
- Sampling: node sends every 750 ms.
- Flame debounce: 5 consecutive HIGH readings ≈ 0.75–1 s. Brief flicker → no trigger.
- Flame recovery: on removal, fire contribution decays linearly to 0 over 5 s (no instant snap).
- Gas: normalize raw ADC → 0.0–1.0; **ignore all gas readings for first 30 s after node boot** (warm-up); contribution rises proportionally, no step jump.
- Water: normalized 0.0–1.0, proportional contribution; resets correctly when dried.
- PIR: occupancy change logged only if new state holds ≥ 1.5 s (no log spam on flicker).
- OFFLINE: no reading from a zone for > 3 s (4× interval) → zone OFFLINE. Per-sensor: payload field null/missing → that sensor OFFLINE; if any hazard sensor is offline the zone card shows an OFFLINE badge, never a false SAFE.

## Priority ranking (locked — dashboard must show the breakdown)

For all currently-CRITICAL zones:

```
priority = risk_score
         + 15 * occupancy_factor              (people present → jump the queue)
         + min(10, unacked_seconds / 15)      (escalates while ignored, cap +10)
Tie-break: earlier CRITICAL entry first.
```

Dashboard shows per-zone justification line, e.g. `Risk 78 + Occupied +15 + unacked 90s +6 = 99`. (TC12c is 4 marks purely for this visibility.)

## Database schema (locked)

```sql
zones(id PK, name UNIQUE, api_key UNIQUE, created_at)
sensors(id PK, zone_id FK→zones ON DELETE RESTRICT, type, status)        -- status: online/offline
readings(id PK, zone_id FK→zones, seq INT, fire INT, gas_norm REAL,
         water_norm REAL, occupancy INT, ts_device, ts_server,
         UNIQUE(zone_id, seq))                                            -- dedup for TC6d
zone_transitions(id PK, zone_id FK, from_state, to_state, risk_score,
                 cause TEXT, ts)                                          -- cause: 'sensor'|'manual'; timeline rows
incidents(id PK, zone_id FK→zones ON DELETE RESTRICT, opened_at,
          peak_risk, status TEXT, resolved_at)                            -- status: open/acked/resolved
acknowledgments(id PK, incident_id FK→incidents UNIQUE, user_id FK, ts)  -- UNIQUE = race safety
users(id PK, username UNIQUE, password_hash, role TEXT, token)           -- role: 'staff'|'admin' (fulfills Users_Roles)

CREATE INDEX idx_incidents_status_created ON incidents(status, opened_at);
CREATE INDEX idx_readings_zone_ts ON readings(zone_id, ts_server);
```

Rules:
- State transitions stored as rows, never overwriting a single "current state" field → incident timeline is free (TC14).
- Ack race: `INSERT INTO acknowledgments ... ON CONFLICT DO NOTHING`, check rowcount → exactly one recorded, loser gets 409.
- Zone delete with open incidents → blocked by `ON DELETE RESTRICT` (TC18b — demo this).
- Out-of-order timestamp: state logic keyed on `ts_server` arrival order; a reading with `ts_device` earlier than the zone's last applied reading is stored + flagged `anomaly`, never rewrites current state (TC18c).
- Query for TC19 must run fast on 10k+ seeded rows; docs state the `(status, opened_at)` index and why. Include `sim/seed.py` to insert 10k readings.

## API (locked — this table goes verbatim into docs, TC8/TC28)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/ingest` | zone API key header `X-Zone-Key` | raw readings in, validated, deduped by seq |
| GET | `/api/zones/status` | token | all zones' current state in one call |
| GET | `/api/incidents?from=&to=&zone=&status=` | token | history, date-range filter required |
| POST | `/api/incidents/{id}/ack` | staff/admin | 404 unknown id, 409 already acked |
| POST | `/api/admin/override` | **admin only, enforced backend-side** | manual state set with reason, cause='manual' |
| POST | `/api/auth/login` | — | returns token + role |
| GET | `/api/commands/{zone_id}` | zone key | node polls actuation commands |
| WS | `/ws?token=` | token | pushes state_change / priority_update / incident events |
| GET | `/api/zones/{id}/trend` | token | Bonus 2 |
| POST | `/api/report` | staff/admin | Bonus 4 NL reporting |
| GET | `/api/health` | admin | system health for admin view |

Every endpoint documented with one example request/response JSON.

**RBAC (TC13, 6 marks)**: role checked at the top of every admin handler. Demo both: staff blocked in UI *and* staff token → direct `curl` to `/api/admin/override` → 403.

## Backend behavior checklist (each maps to a scored test)

- Pydantic validation with hard ranges; negative water / gas > 1.0 / non-bool occupancy → 422 with clear error body (TC6b, TC23f).
- Duplicate `(zone_id, seq)` → 200 `{"duplicate": true}`, not counted twice (TC6d).
- Two hazards crossing at once → combined score from the real formula (TC6c).
- All zones posting simultaneously → none dropped; async handlers (TC6e).
- Rapid CRITICAL→SAFE→CRITICAL flip: hysteresis + 3 s hold → no incident flood (TC7c). Re-trigger after an incident is resolved → **new** incident row (TC7d).
- Actuation fires on state *entry* (edge-triggered, not per-reading) → override + sensor at same instant can't double-fire; `cause` column shows which one acted (TC23c).
- CRITICAL → command to that node within 1 s: buzzer + red LED + relay, logged with timestamp. WARNING → yellow LED only, no relay/buzzer (TC5).
- Startup: load last transition + open incidents per zone from DB before serving (TC9a).
- Node offline caching: firmware/sim buffers readings in RAM when POST fails, resyncs with original seq numbers on reconnect (TC9b).
- Unregistered zone key → 401 (TC10a). Dashboard call without token → 401 (TC10b).
- Load: `sim/driver.py --phantom 30` fires 30 fake zones; backend + dashboard stay responsive; docs include a paragraph on scaling to a real 30+ zone campus (uvicorn workers, Postgres swap, MQTT ingestion, WS fan-out via Redis pub/sub) (TC11).

## Frontend spec (single dark-theme page, security-ops aesthetic)

- **Zone grid**: card per zone — color + icon + text label (never color alone, TC16b), live risk score, per-sensor mini-readouts, OFFLINE badge. Updates via WS, zero manual refresh (TC12a).
- **Priority queue panel**: appears when ≥1 CRITICAL; ranked list with the justification breakdown line per zone (TC12b/c). #1 entry pulses + top banner `⚠ MOST URGENT: <zone>` → any first-time viewer identifies it in 2 s (TC12d).
- **Incident timeline**: filterable table (zone, hazard, status, date range) → click an incident → full transition timeline first-trigger → ack (who/when) → recovery (TC14).
- **Notifications**: stacking toasts + short audio cue on new CRITICAL; each alert distinct; ack stops the pulsing/audio for that incident (TC15).
- **Roles**: login page; staff sees zones + ack; admin additionally sees override panel + system health. Buttons hidden in UI *and* enforced by API (TC13).
- **Reconnect**: WS drop → auto-reconnect → full `GET /api/zones/status` refetch, no stale data (TC23d).
- **Bonus panels**: trend arrow/sparkline per zone ("trending toward CRITICAL") and a visually separate `Predicted Risk` chip (distinct styling + label, never blended with live score).

## Simulator / scenario driver (`sim/driver.py`) — the video engine

Named scenarios, one per rubric test, each printing narration cues:

```
tc1a..tc1d  flame: none / flicker < debounce / sustained / removal decay
tc2a..tc2d  gas: baseline / gradual ramp / threshold cross / boot warm-up
tc3a..tc3d  water: dry / rising / cross / wet-then-cleared
tc4a..tc4d  pir: empty / enter / flicker / sensor disconnected → OFFLINE
tc5a..tc5d  actuation: critical response / warning-only / recovery reset / two zones same second
tc6b/6c/6d/6e  malformed payload / dual-hazard / duplicate seq / concurrent all-zones
tc7a..tc7d  3 zones in 1 s / double-ack race (two parallel requests) / flip-flood / re-trigger
tc11a       --phantom 30 load
tc18a       10 simultaneous writes
tc22        finale: full end-to-end two-zone incident (the continuous demo)
tc23x       edge cases: offline mid-incident / triple-critical / override-collision /
            reconnect catch-up / restart recovery / impossible value
```

Driver targets Wokwi nodes where visual (slider/click on Wokwi UI is *better* footage) and hits the API directly for protocol-level cases (duplicate, malformed, race). Every scenario idempotent and re-runnable — video takes will be re-shot.

## Bonuses (only after TC22 finale passes end-to-end; ordered by marks/hour)

1. **Bonus 2 — Short-term risk trend (10)**: slope of last 8 risk scores per zone; `rising` flag when slope > threshold and score in WARNING band; arrow + "trending toward CRITICAL" on card. ~1 h.
2. **Bonus 3 — ML risk prediction (10)**: logistic regression (scikit-learn) on **synthetic data — stated clearly**: generate labeled windows from the simulator (features: last-N gas/water slopes, fire flag, occupancy; label: CRITICAL within 120 s). Report accuracy + precision/recall in docs. Serve P(critical) as the separate `Predicted Risk` chip. Hard rule in code: prediction path has no write access to actuation. Safety statement (e) printed in docs and README.
3. **Bonus 4 — NL incident reporting (10)**: `POST /api/report` takes free text → LLM call (env `LLM_API_KEY`, provider-agnostic, DeepSeek/OpenRouter) with strict JSON schema `{zone, hazard_type, severity 0–1}` → **deterministic validation gate**: zone must exist, hazard in enum, severity clamped; on fail → rejected with reason. Valid reports enter ranking as a soft advisory term (+ up to 10 priority, decays over 10 min), never actuation. Reply confirms what was understood. Fallback: if no API key, regex/keyword parser behind the same gate — demo still works offline.
4. **Bonus 1 — skip** (needs physical ESP32-CAM; not worth it on Track B).

## Phases — working agreement for Claude Code

- **Phase 0**: repo scaffold, venv, deps, `init_db.py`, seed script (3 zones w/ API keys, users `staff1`/`admin1`), `.env.example`, run instructions. Commit.
- **Phase 1**: ingestion → validation → dedup → fusion → state machine (hysteresis, decay, warm-up) → transitions → incidents → ack (constraint-backed) → auth/RBAC. `pytest` covering: formula math, debounce, hysteresis, duplicate seq, ack race (two concurrent), 404/409/401/403/422 paths. Commit.
- **Phase 2**: WS broadcast + full dashboard (grid, queue + justification, timeline, toasts+audio, login, admin panel, reconnect catch-up). Commit.
- **Phase 3**: `sim/` zone simulators + all driver scenarios + resilience (restart recovery, offline caching, phantom load, 10k seed). Run every scenario once — all green. Commit.
- **Phase 4**: `firmware/zone_node.ino` + `firmware/diagram.json` (Wokwi project), actuation poll loop, LED/buzzer/relay wiring per zone. Commit.
- **Phase 5**: Bonuses 2 → 3 → 4. **Cut rule: if < 8 h to deadline, stop after Bonus 2 and move to Phase 6.**
- **Phase 6**: `DOCUMENTATION.md` (→ export PDF): circuit diagram per zone (Wokwi diagram screenshots + pin table clear enough to rebuild), architecture diagram (mermaid, matches Section 03 shape, data-flow arrows), full API table with example payloads, **mermaid ER diagram** (not a bullet list), risk formula + weight justification, backup strategy (`scripts/backup.sh` running `sqlite3 .backup` to `backups/`, restore path + gap description), retention/access policy ("raw readings > 90 days summarized then dropped; only admins query raw history — ties to TC13 roles"), scaling note. Plus `README.md` (setup in < 10 commands), `VIDEO_SCRIPT.md`, `SUBMISSION.md` checklist. Commit, tag `r1-final`.

Rules of engagement: never leave `main` broken; after each phase run the driver smoke suite; make sensible defaults instead of asking — log every assumption in `ASSUMPTIONS.md`; small commits with test-case IDs in messages (judges read the repo).

## VIDEO_SCRIPT.md requirements (TC31 — content past 7:00 is not reviewed)

Budget table with hard timestamps; every segment titled on-screen with its test-case ID:

```
0:00–0:20  intro: system live in idle state, all three zones SAFE on dashboard + Wokwi
0:20–2:00  Section A: tc1–tc5 rapid labeled cuts (sensor closeups + dashboard reaction)
2:00–3:20  Section B: malformed reject, duplicate seq, double-ack race (two terminals), restart recovery
3:20–4:30  Section C: live map, priority queue + justification, RBAC (UI block + curl 403), timeline
4:30–5:10  Section D: schema/ER on screen, delete-zone blocked live, indexed query on 10k rows
5:10–6:20  Section E: tc22 finale continuous — two zones CRITICAL, ranked, acked in order, recovery to idle
6:20–6:55  Bonuses: trend flag, predicted-risk chip + "never actuates" statement, NL report demo
6:55–7:00  close
```

Narration rule: say what the judge is seeing *and which test case it satisfies*, as it happens.

## Submission checklist (SUBMISSION.md)

- [ ] Public GitHub repo: firmware/ (Wokwi), backend/, frontend/, sim/, schema + migrations, README. **No pushes after submitting.**
- [ ] Documentation PDF exported from DOCUMENTATION.md — everything in Section 11 of the case.
- [ ] Video ≤ 7:00, Google Drive, general viewer access.
- [ ] File naming: `RoboFusion_[SegmentName]_Error404_R1` (confirm SegmentName convention with organizers: Mumith Chowdhury mumith0001@std.uftb.ac.bd / Ahmed Shahariar Udoy shahariar0001@std.uftb.ac.bd).
- [ ] Formula weights in docs match the code exactly (TC30 checks consistency).
- [ ] Wokwi project link(s) included in README.

## Kickoff

Read this file fully, then execute Phase 0 and Phase 1 without pausing for questions. Surface blockers only if a default genuinely can't be chosen.
