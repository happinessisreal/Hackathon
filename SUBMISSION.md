# SUBMISSION.md

RoboFusion 1.0 Techathon, Round 1 (SCS-RG). Deadline: **27 July 2026,
12:00 AM BST**. Checklist below tracks what's done vs. what's still a human
task — check items off as they're actually completed, don't pre-check.

## Repo

- [x] Public GitHub repo pushed with: `firmware/` (Wokwi), `backend/`,
      `frontend/`, `sim/`, schema (in `backend/models.py` + `DOCUMENTATION.md`
      §6), `README.md`.
- [ ] Repo is actually set to **public** on GitHub (not just pushed to a
      private remote) — verify in repo Settings.
- [ ] **No pushes after submitting.** Once the form is in, treat the repo
      as frozen — including this file.
- [ ] Wokwi project link(s) added to `README.md` (placeholder currently —
      fill in once each zone's Wokwi project is saved/shared).

## Code / functional completeness

- [x] Phase 0 — scaffold, DB schema, seed script, `.env.example`.
- [x] Phase 1 — ingestion, fusion, state machine, incidents, ack, auth/RBAC
      (71 pytest cases, all green).
- [x] Phase 2 — WS broadcast + full dashboard.
- [x] Phase 3 — `sim/` simulators + driver scenarios (tc1–tc23, all green)
      + resilience (phantom load, 10k seed).
- [x] Phase 4 — `firmware/zone_node.ino` + `firmware/diagram.json`, written
      and cross-checked against the backend contract and Wokwi's part docs.
- [ ] **Firmware has not been smoke-tested in the actual Wokwi simulator or
      on real hardware from this environment.** This is the single biggest
      open risk to the video — do this before the Wokwi-dependent parts of
      `VIDEO_SCRIPT.md` are recorded. See `firmware/README.md` "Validation
      status."
- [x] Phase 5 — **all four bonuses attempted**: Bonus 1 (camera occupancy
      cross-check — frame-difference detection standing in for the
      ESP32-CAM on Track B, honestly documented), Bonus 2 (risk trend UI),
      Bonus 3 (ML prediction — synthetic-data logistic regression, metrics
      reported, no-actuation guard tested), Bonus 4 (NL incident
      reporting, DeepSeek-backed with an offline fallback).
- [x] Case-PDF audit pass: TC14 hazard-type + duration columns/filter,
      TC16b accessibility statement, TC24 driver scenario, TC26 per-zone
      diagram files, TC9b firmware offline buffer — all closed.

## Documentation

- [x] `DOCUMENTATION.md` written — architecture (mermaid), risk formula +
      justification, priority formula, ER diagram (mermaid), full API table
      with example payloads, RBAC, backup/restore, retention/access policy,
      scaling note, track declaration, testing summary.
- [ ] Wokwi screenshots inserted into `DOCUMENTATION.md` §11 (needs the
      human Wokwi smoke-test first — see above).
- [ ] `DOCUMENTATION.md` exported to PDF (Pandoc / VS Code "Markdown PDF" /
      equivalent) and named per the convention below.
- [x] `ASSUMPTIONS.md` — every default chosen without pausing, logged with
      reasoning, kept current through Phase 5.
- [x] `README.md` — setup in under 10 commands, test/driver instructions,
      firmware section, status section reflects actual phase completion.

## Video

- [x] `VIDEO_SCRIPT.md` — full timestamped budget table (0:00–7:00), every
      segment tagged with its test-case IDs, narration rules, and a
      pre-recording checklist (fresh DB, LLM path verified live, two
      terminals staged for the ack race, RBAC curl ready).
- [ ] **Recording not yet done.** Blocked on the Wokwi smoke-test (firmware
      must actually run before it can be filmed).
- [ ] Before recording: verify `/api/report` actually returns
      `"source": "llm"` if `LLM_API_KEY` is set — `nl_report.py` silently
      falls back to the offline parser on any failure, so this must be
      confirmed off-camera or the on-camera narration should say "offline
      parser" instead of "LLM."
- [ ] Before recording (Bonus 1): start the camera node
      (`python sim/camera_node.py --zone "IoT Lab" --webcam`, needs
      `pip install opencv-python`; `--synthetic` works with no extra
      install but the narration must then say "scripted pattern"). Confirm
      the CAM chip renders on the zone card.
- [ ] Video is ≤ 7:00.
- [ ] Uploaded to Google Drive with **general viewer access** (test the
      link in a private/incognito window, logged out).

## Formula/consistency check (TC30)

- [x] Weights in `backend/config.py` (`WEIGHT_FIRE=40, WEIGHT_GAS=25,
      WEIGHT_WATER=25, WEIGHT_OCCUPANCY=10`) match `DOCUMENTATION.md` §3 and
      `CLAUDE.md` exactly — verify again after any late code change, since
      this is checked verbatim.
- [x] Priority formula constants (`PRIORITY_OCCUPANCY_BONUS=15,
      PRIORITY_UNACKED_CAP=10, PRIORITY_UNACKED_DIVISOR=15`, Bonus-4
      advisory cap = 10 / decay = 600s) match `DOCUMENTATION.md` §5.

## File naming (confirm with organizers before finalizing)

- [ ] `RoboFusion_[SegmentName]_Error404_R1` — confirm the exact
      `SegmentName` convention with Mumith Chowdhury
      (mumith0001@std.uftb.ac.bd) or Ahmed Shahariar Udoy
      (shahariar0001@std.uftb.ac.bd) before naming the final PDF/video
      files.

## Final housekeeping

- [x] Team name `Error404` substituted throughout (search the whole
      repo — `README.md`, `DOCUMENTATION.md`, `VIDEO_SCRIPT.md`, this file,
      and any on-screen text/slides in the video) with the actual team
      name.
- [ ] `git tag r1-final` once the repo content above is genuinely final
      (after the Wokwi test and any resulting fixes — not before).
- [ ] Double-check `.env` was never committed (`git log --all -- .env`
      should return nothing) — it's gitignored, but worth one last check
      before making the repo public.
