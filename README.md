# SCS-RG - Multi-Hazard Smart Campus Safety & Response Grid

RoboFusion 1.0 Techathon, Round 1. Team: `[TEAM_NAME]`.

Track B (Wokwi ESP32) primary; `sim/` Python simulators are used for load and
protocol-level edge-case testing only (duplicate seq, malformed payloads,
concurrent races) - not as a substitute for the Wokwi hardware demo.

## Status

- **Phase 0** (scaffold, schema, seed): done.
- **Phase 1** (ingestion, fusion, state machine, incidents, ack, auth/RBAC, tests): done.
- **Phase 2** (WS + dashboard), **Phase 3** (sim/driver scenarios), **Phase 4**
  (firmware), **Phase 5** (bonuses 2-4), **Phase 6** (docs/video/submission):
  not yet started.

See `ASSUMPTIONS.md` for defaults chosen without pausing for confirmation.

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

Open http://localhost:8000/api/ping to confirm the backend is up. The
dashboard (`frontend/`) is served from the same origin once Phase 2 lands.

## Tests

```bash
pytest -q
```

40 tests covering: fusion formula math, flame debounce/decay, PIR hold,
CRITICAL hysteresis (entry/exit/min-hold/flip-flood suppression), duplicate
seq dedup, out-of-order/anomaly flagging, incident open/resolve/re-trigger,
concurrent ack race (exactly-once via DB unique constraint), and
401/403/404/409/422 auth/RBAC/validation paths.

## API

See the locked table in `CLAUDE.md`; full request/response examples land in
`DOCUMENTATION.md` (Phase 6).

## Backup

```bash
scripts/backup.sh   # sqlite3 .backup, safe to run live (WAL mode)
```
