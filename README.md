# SCS-RG - Multi-Hazard Smart Campus Safety & Response Grid

RoboFusion 1.0 Techathon, Round 1. Team: `[TEAM_NAME]`.

Track B (Wokwi ESP32) primary; `sim/` Python simulators are used for load and
protocol-level edge-case testing only (duplicate seq, malformed payloads,
concurrent races) - not as a substitute for the Wokwi hardware demo.

## Status

- **Phase 0** (scaffold, schema, seed): done.
- **Phase 1** (ingestion, fusion, state machine, incidents, ack, auth/RBAC, tests): done.
- **Phase 2** (WS broadcast + full dashboard): done.
- **Phase 3** (sim/driver scenarios), **Phase 4** (firmware), **Phase 5**
  (bonuses 2-4), **Phase 6** (docs/video/submission): not yet started.

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

Open http://localhost:8000/ for the dashboard (login with the credentials
printed above) or http://localhost:8000/api/ping to confirm just the backend
is up.

## Tests

```bash
pytest -q
```

45 tests covering: fusion formula math, flame debounce/decay, PIR hold,
CRITICAL hysteresis (entry/exit/min-hold/flip-flood suppression), duplicate
seq dedup, out-of-order/anomaly flagging, incident open/resolve/re-trigger,
concurrent ack race (exactly-once via DB unique constraint), restart
recovery (rebuilds state from DB, not SAFE), WS snapshot/broadcast, and
401/403/404/409/422 auth/RBAC/validation paths. Every automated pass has
also been re-verified live: a real uvicorn process + real WebSocket client
(no TestClient), and the dashboard driven end-to-end in an actual browser
(login, override, live push, ack, resolve, timeline modal, RBAC hiding).

## API

See the locked table in `CLAUDE.md`; full request/response examples land in
`DOCUMENTATION.md` (Phase 6).

## Backup

```bash
scripts/backup.sh   # sqlite3 .backup, safe to run live (WAL mode)
```
