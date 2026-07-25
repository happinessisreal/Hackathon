# VIDEO_SCRIPT.md

7:00 hard cap — content past 7:00 is not reviewed. Every segment is titled
on-screen with its test-case ID; narration says **what the judge is seeing
and which test case it satisfies**, as it happens, per CLAUDE.md.

**Pre-recording checklist (do this before hitting record, not during):**

1. `python scripts/init_db.py` against a **fresh** `data/scsrg.db` (delete
   the old one first) so the demo starts from a clean, empty-history state.
2. Start the backend: `uvicorn backend.main:app --port 8000`.
3. Open the Wokwi project(s) for all 3 zones; confirm Serial shows
   `WiFi connected` / `time synced` and the dashboard shows all 3 zones
   SAFE before recording starts (this *is* the 0:00 shot).
4. **Verify the real LLM path once, off-camera**, if `LLM_API_KEY` is set
   in `.env`: submit one `/api/report` and confirm the response says
   `"source": "llm"`, not `"fallback"`. `call_llm()` silently degrades to
   the offline parser on any error/timeout — if it's not actually reaching
   DeepSeek, the on-camera narration must say "offline keyword parser,"
   not "LLM," or the claim doesn't match what's on screen.
5. **Camera node (Bonus 1)**: start
   `python sim/camera_node.py --zone "IoT Lab" --webcam` (or `--video
   footage.mp4`; `--synthetic` as last resort — narration must then say
   "scripted pattern demonstrating the integration"). Confirm the `CAM`
   chip appears on the IoT Lab card before recording.
6. **Model artifact (Bonus 3)**: confirm `ml/model.json` exists (ships in
   the repo; `python ml/train.py` regenerates it) and that a WARNING-band
   zone shows the violet `Predicted Risk` chip.
7. Have two terminals ready for the double-ack race (Section B) and one
   more for the RBAC `curl` 403 (Section C).
8. `python sim/seed.py --readings 10000 --incidents 300` against a
   **separate** DB/port beforehand if you want the 10k-row timing shot to
   use real numbers instead of a smaller live count — see Section D.

---

| Segment | Time | Covers |
|---|---|---|
| Intro | 0:00–0:20 | System live, idle |
| A | 0:20–1:55 | tc1–tc5 |
| B | 1:55–3:05 | Malformed / dup-seq / ack race / restart |
| C | 3:05–4:10 | Priority queue, RBAC, timeline (+hazard/duration) |
| D | 4:10–4:45 | Schema/ER, delete-blocked, indexed query |
| E | 4:45–5:45 | tc22 finale + tc24 load + tc25 consistency |
| Bonus | 5:45–6:55 | All four: camera, trend, ML prediction, NL report |
| Close | 6:55–7:00 | Wrap |

---

## 0:00–0:20 — Intro

**Show**: dashboard full-screen, all 3 zones SAFE, green LEDs on the Wokwi
boards visible in a second window/PiP.

**Say**: "SCS-RG — Multi-Hazard Smart Campus Safety & Response Grid, Team
`[TEAM_NAME]`. Three zones, live on Wokwi ESP32 nodes, all currently SAFE.
Every score you'll see is computed server-side from raw sensor values —
the nodes never send a state, only numbers."

## 0:20–1:55 — Section A: tc1–tc5

Rapid labeled cuts: sensor action in Wokwi → dashboard reaction, cut fast.
Drive via the Wokwi UI directly (click-and-hold the flame pushbutton, drag
the water potentiometer, click the PIR) — better footage than the Python
sim for this section.

- **[tc1a–tc1d] Flame** (~22s): no flame → SAFE. Quick flicker (under
  ~3.75s / 5 readings) → "flicker ignored — debounce working." Hold past 5
  readings → "sustained flame, fire contributes 40 points." Release →
  "watch the score decay smoothly over 5 seconds, not snap to zero."
- **[tc2a–tc2d] Gas** (~18s, IoT Lab only): baseline 0. Slow ramp →
  "proportional rise, no step jump." Mention warm-up: "for the first 30
  seconds after a node boots, gas readings are ignored entirely — no false
  trigger on cold start."
- **[tc3a–tc3d] Water** (~18s, Server Room): dry → SAFE. Raise the
  potentiometer → "rising, proportional — our flood-equivalent hazard,
  weighted equal to gas because these are server rooms." Cross the band.
  Lower it → "cleared, resets correctly, not sticky."
- **[tc4a–tc4d] PIR** (~18s, Data Science Lab): empty. Click PIR, hold past
  1.5s → "occupancy commits, +10." Quick flicker → "under the hold,
  correctly ignored." Disconnect the sensor → "OFFLINE badge — offline is
  never shown as safe."
- **[tc5a–tc5d] Actuation** (~19s): drive CRITICAL → "buzzer, red LED and
  relay all fire within about a second of state entry." Drop to WARNING →
  "yellow only, no relay, no buzzer." Back to SAFE → "reset, logged." Two
  zones CRITICAL same second → "each responds independently."

## 1:55–3:05 — Section B: protocol edge cases + restart recovery

Terminal + dashboard side by side.

- **[tc6b] Malformed payload** (~13s): `curl` a negative `water_norm` →
  `422` body on screen. "Rejected with a clear field error, never silently
  absorbed."
- **[tc6d] Duplicate seq** (~13s): same `seq` twice → `"duplicate": true`.
  "Not counted twice — the score doesn't move on the replay."
- **[tc7b] Double-ack race** (~25s): zone CRITICAL, two `curl` acks from
  two terminals at once → one `200`, one `409`. "Exactly one
  acknowledgment — enforced by a database unique constraint, not
  application logic, so it holds under a real race."
- **[TC9a] Restart recovery** (~19s): with an incident open, kill and
  restart `uvicorn` on camera. Refresh → still CRITICAL, incident still
  open. "Rebuilt from the database on startup — never assumes SAFE."

## 3:05–4:10 — Section C: priority queue, RBAC, timeline

- **[TC12a–d] Priority queue** (~22s): ≥2 zones CRITICAL, show the queue,
  the pulsing `#1`, the `⚠ MOST URGENT` banner. Read one justification
  line aloud: "`Risk 78 + Occupied +15 + unacked 90s +6 = 99` — every
  point in the ranking is visible, not a black box."
- **[TC13] RBAC** (~22s): log in as `staff1` — "no override panel, no
  health tab." Then `curl POST /api/admin/override` with the staff token →
  `403`. "Enforced server-side too — hiding the button isn't the security
  boundary."
- **[TC14] Timeline** (~21s): show the incident table — point at the
  **Hazard** column ("fire+water — the dominant contributions at the
  moment the incident opened; manual for override-opened ones") and the
  **Duration** column. Filter by hazard = water on camera. Click an
  incident → modal: first-trigger → ack (who/when) → recovery. "Every row
  is a stored transition, nothing is reconstructed."

## 4:10–4:45 — Section D: schema, delete-block, indexed query

- **[TC17/TC29 Schema]** (~12s): ER diagram from `DOCUMENTATION.md` §6 on
  screen. "Six related tables, every FK explicit, ON DELETE RESTRICT
  everywhere."
- **[TC18b] Delete-zone blocked** (~10s): attempt to delete a zone with an
  open incident → RESTRICT failure on screen. "History can't be orphaned."
- **[TC19] Indexed query on 10k+ rows** (~13s): `GET /api/incidents?...`
  against the seeded DB, show the response time. "The `(status,
  opened_at)` index — stated in the docs with reasoning — keeps this
  instant at 10,000+ rows."

## 4:45–5:45 — Section E: integration (tc22 + tc24 + tc25)

- **[TC22] Finale** (~40s, one continuous take — run
  `python sim/driver.py tc22` against the live server, or drive both zones
  from the Wokwi UI): "IoT Lab and Server Room building toward CRITICAL
  concurrently... both crossed — the queue ranks them, occupancy and
  unacked time factored in... acknowledging in priority order... and
  recovery — both back to SAFE, idle state."
- **[TC24] Combined load** (~12s): `python sim/driver.py tc24` — "three
  zones live, one cycling SAFE→WARNING→CRITICAL rapidly; status stays
  correct and the API stays responsive — worst-case latency on screen."
- **[TC25] Consistency** (~8s): pause on one zone — point at the Wokwi
  LED, the API response, and the dashboard card in the same frame. "Same
  state in all three places, because there's only one source of truth."

## 5:45–6:55 — Bonuses (all four attempted)

- **[Bonus 1] Camera occupancy cross-check** (~20s): with the camera node
  running, cover the PIR (or use a zone where PIR reads empty) while
  moving in front of the webcam → CAM chip shows `occupied`, disagreement
  highlighted. If that zone is CRITICAL, show the justification line:
  `Occupied (camera) +15`. **Say**: "Frame-difference detection on a
  [webcam / video / scripted pattern] standing in for the ESP32-CAM on
  Track B — it cross-checks the PIR and can rescue a false 'empty' in the
  priority ranking. It never touches the risk score."
- **[Bonus 2] Trend** (~10s): WARNING zone's card → sparkline + "trending
  toward CRITICAL" chip. "Slope of the last 8 scores — early warning
  before the threshold."
- **[Bonus 3] ML prediction** (~20s): violet `Predicted Risk N% (ML,
  advisory)` chip on a rising zone. **Say**: "A logistic regression
  trained on synthetic data — stated plainly in the docs with accuracy
  0.84, precision 0.45, recall 0.86 on held-out data. It's a separate
  advisory chip, never mixed with the live score, and the prediction path
  has no code route to the relay or buzzer — there's a test that fails the
  build if anyone ever wires it in."
- **[Bonus 4] NL report** (~20s): with Server Room still CRITICAL, type
  "small water leak near the racks in Server Room" → confirmation reply →
  queue justification updates with `+ NL report +N`. **Say**: "Free text →
  [LLM / offline parser] → a deterministic validation gate — zone must
  exist, hazard in the enum, severity clamped — before it may touch the
  ranking. Advisory only, decays over 10 minutes, never actuates."

## 6:55–7:00 — Close

**Show**: dashboard back at idle, all zones SAFE.

**Say**: "SCS-RG, Team `[TEAM_NAME]` — Track B, all four bonuses,
backend-only scoring, one source of truth, every actuation traceable to a
real sensor reading. Thank you."
