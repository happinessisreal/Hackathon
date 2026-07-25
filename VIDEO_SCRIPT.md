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
5. Have two terminals ready for the double-ack race (Section B) and one
   more for the RBAC `curl` 403 (Section C).
6. `python sim/seed.py --readings 10000 --incidents 300` against a
   **separate** DB/port beforehand if you want the 10k-row timing shot to
   use real numbers instead of a smaller live count — see Section D.

---

| Segment | Time | Covers |
|---|---|---|
| Intro | 0:00–0:20 | System live, idle |
| A | 0:20–2:00 | tc1–tc5 |
| B | 2:00–3:20 | Malformed / dup-seq / ack race / restart |
| C | 3:20–4:30 | Priority queue, RBAC, timeline |
| D | 4:30–5:10 | Schema/ER, delete-blocked, indexed query |
| E | 5:10–6:20 | tc22 finale |
| Bonus | 6:20–6:55 | Trend, NL report |
| Close | 6:55–7:00 | Wrap |

---

## 0:00–0:20 — Intro

**Show**: dashboard full-screen, all 3 zones SAFE, green LEDs on the Wokwi
boards visible in a second window/PiP.

**Say**: "SCS-RG — Multi-Hazard Smart Campus Safety & Response Grid, Team
`[TEAM_NAME]`. Three zones, live on Wokwi ESP32 nodes, all currently SAFE.
Every score you'll see is computed server-side from raw sensor values —
the nodes never send a state, only numbers."

## 0:20–2:00 — Section A: tc1–tc5

Rapid labeled cuts: sensor action in Wokwi → dashboard reaction, cut fast.
Drive via the Wokwi UI directly (click-and-hold the flame pushbutton, drag
the water potentiometer, click the PIR) — better footage than the Python
sim for this section, per the driver's own design intent.

- **[tc1a–tc1d] Flame** (~25s): no flame → SAFE. Quick flicker (under
  ~3.75s / 5 readings) → "flicker ignored, no trigger — debounce working."
  Hold past 5 readings → "sustained flame, fire contributes 40 points."
  Release → "removal — watch the score decay smoothly over 5 seconds, not
  snap to zero."
- **[tc2a–tc2d] Gas** (~20s, IoT Lab only): baseline 0. Slow ramp on the
  gas sensor → "proportional rise, no step jump." Mention warm-up: "for
  the first 30 seconds after a node boots, gas readings are ignored
  entirely — prevents a false trigger on cold start" (cut to a freshly-
  booted Serial log timestamp if available, or state it and move on).
- **[tc3a–tc3d] Water** (~20s, Server Room / Data Science Lab): dry → SAFE.
  Raise the potentiometer → "rising, proportional — this is our
  flood-equivalent hazard, weighted equal to gas because these are server
  rooms." Cross into WARNING/CRITICAL range. Lower it back → "cleared,
  resets correctly, not sticky."
- **[tc4a–tc4d] PIR** (~20s, Data Science Lab): empty. Click PIR → hold
  past 1.5s → "occupancy commits, +10 to score." Quick double-click
  (flicker) → "held under 1.5s, correctly ignored — no log spam." Disable/
  disconnect the sensor → "OFFLINE badge appears — offline is never shown
  as safe."
- **[tc5a–tc5d] Actuation** (~15s): trigger CRITICAL on one zone → "buzzer
  and red LED fire within about a second of the state entering CRITICAL —
  watch the relay module too." Drop to WARNING → "yellow LED only, no
  buzzer, no relay." Back to SAFE → "green, everything off." Trigger two
  zones CRITICAL the same second (can run
  `python sim/driver.py tc5 --base-url http://127.0.0.1:8801` against a
  throwaway port beforehand and just narrate the recorded result, or do it
  live with two Wokwi tabs) → "both act independently and correctly."

## 2:00–3:20 — Section B: protocol edge cases + restart recovery

Switch to a terminal + the dashboard side by side.

- **[tc6b] Malformed payload** (~15s): `curl` a negative `water_norm` at
  `/api/ingest` → show the `422` body on screen. "Rejected with a clear
  field error, never silently absorbed or clamped."
- **[tc6d] Duplicate seq** (~15s): POST the same `seq` twice → second
  response shows `"duplicate": true`. "Not counted twice — score doesn't
  move on the replay."
- **[tc7b] Double-ack race** (~30s): drive one zone CRITICAL, then fire
  two `curl` acks from two terminals **at the same time** (or
  `python sim/driver.py tc7` and narrate the captured result) → one `200`,
  one `409`. "Exactly one acknowledgment — enforced by a database unique
  constraint, not just application logic, so this holds even under a real
  race."
- **[TC9a] Restart recovery** (~20s): with an incident still open, kill
  and restart `uvicorn` on camera. Refresh the dashboard → the zone is
  still CRITICAL, the incident still open. "The backend rebuilt this from
  the database on startup — it never assumed SAFE just because it
  restarted."

## 3:20–4:30 — Section C: priority queue, RBAC, timeline

- **[TC12a–d] Priority queue** (~25s): with ≥2 zones CRITICAL (carry state
  over from Section B or trigger fresh), show the queue panel, the pulsing
  `#1` entry, and the `⚠ MOST URGENT` banner. Read one justification line
  aloud: "`Risk 78 + Occupied +15 + unacked 90s +6 = 99` — every point in
  that ranking is visible, not a black box."
- **[TC13] RBAC** (~25s): log in as `staff1` — "no override panel, no
  health tab, hidden entirely from the UI." Then, in a terminal, `curl
  POST /api/admin/override` with the staff token → `403`. "Enforced
  server-side too — hiding the button isn't the security boundary."
- **[TC14] Timeline** (~20s): click an incident → modal shows
  first-trigger → ack (who, when) → recovery as a real row-per-transition
  timeline. "Nothing here is reconstructed — every row is a stored
  transition."

## 4:30–5:10 — Section D: schema, delete-block, indexed query

- **[Schema]** (~15s): show the ER diagram from `DOCUMENTATION.md` §6 on
  screen. "Every constraint you're about to see enforced live is drawn
  here first."
- **[TC18b] Delete-zone blocked** (~10s): attempt to delete a zone that
  has an open incident (DB browser or a quick script calling the ORM
  delete) → show the `RESTRICT` failure. "A zone with any history — even
  one reading — can't be deleted out from under that history."
- **[TC19] Indexed query on 10k+ rows** (~15s): hit
  `GET /api/incidents?status=open` against the pre-seeded 10k-reading /
  300-incident DB from the pre-recording checklist, show the response
  time (browser devtools network tab or a timed `curl`). "Still fast at
  scale — this is the `(status, opened_at)` index doing its job."

## 5:10–6:20 — Section E: tc22 finale (continuous, no cuts)

Either drive this live via the Wokwi UI (two zones) or run
`python sim/driver.py tc22` against the **live demo server** this one
time (it's the intended finale, not a throwaway-DB scenario) and film the
dashboard reacting in real time.

**Say, continuously, as it happens**: "IoT Lab and Server Room are both
building toward CRITICAL right now, independently — fire and occupancy in
IoT Lab, a simulated coolant leak in Server Room. ... Both just crossed —
watch the priority queue rank them, occupancy and unacked time both
factored in. ... Acknowledging in priority order — highest-priority zone
first. ... And recovery: both back to SAFE, clean idle state, ready for
the next incident."

**Directing note**: if the Bonus section (next) needs a live CRITICAL zone
for the NL-report demo, cut this section's on-camera "recovery" beat short
and leave Server Room acknowledged-but-still-CRITICAL going into 6:20 —
resolve it for real once the Bonus section is done recording.

## 6:20–6:55 — Bonuses

- **[Bonus 2] Trend** (~10s): point at a WARNING zone's card. "Sparkline
  and a rising-trend flag — 'trending toward CRITICAL' — computed from the
  slope of its last 8 scores, purely informational."
- **[Bonus 4] NL incident report** (~25s): **with Server Room still
  CRITICAL from Section E** (see directing note above), type a report —
  e.g. "small water leak near the racks in Server Room" — into the NL
  Report panel and submit. Show the confirmation message. Then show the
  priority queue's justification line updating to include
  `+ NL report +N`. "That's a free-text report, parsed by
  [an LLM / an offline keyword parser — say whichever the pre-recording
  check in step 4 actually confirmed], validated against real zones and
  hazards before it's trusted at all. It only ever adjusts this ranking,
  on a zone sensors already flagged CRITICAL — it cannot create an
  incident, cannot touch the risk score, and cannot trigger the buzzer or
  relay. Only the live sensor-computed score can do that."

*(If Bonus 3 — ML predicted-risk chip — is built before recording, add a
~10s beat here showing the chip and stating "structurally separate from
the live score, and the prediction path has no write access to actuation
at all — not just by convention, there's no code path." Not built as of
this script's writing; see `ASSUMPTIONS.md` Phase 5 for why it was cut in
favor of finishing docs/video on schedule.)*

## 6:55–7:00 — Close

**Show**: dashboard back at idle, all zones SAFE (resolve Server Room from
the Bonus section here, or immediately after cutting).

**Say**: "SCS-RG, Team `[TEAM_NAME]` — Track B, Wokwi ESP32, backend-only
scoring, single source of truth, and every actuation traceable to a real
sensor reading. Thank you."
