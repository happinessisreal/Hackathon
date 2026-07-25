"""CLAUDE.md rule 6: on restart, the backend must rebuild zone state from the
DB before serving - never assume SAFE. This also regression-covers a real
bug: SQLite drops tzinfo on read, so a naive `ZoneTransition.ts` loaded by
restore_from_db compared against a UTC-aware `now` used to raise
"can't subtract offset-naive and offset-aware datetimes" (fixed via the
UTCDateTime TypeDecorator in backend/models.py).
"""

import datetime as dt

from backend.pipeline import process_reading
from backend.schemas import IngestPayload
from backend.state_machine import classify
from backend.zone_manager import CRITICAL, ZoneManager

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


async def test_restore_from_db_survives_process_restart_and_stays_usable(db_session, seeded):
    zone = seeded["zone"]

    for i in range(5):
        payload = IngestPayload(
            seq=i,
            fire=1,
            gas_norm=0.5,
            water_norm=0.4,
            occupancy=1,
            ts_device=T0 + dt.timedelta(seconds=i * 0.75),
            uptime_ms=60_000,
        )
        result = await process_reading(db_session, zone, payload, now=T0 + dt.timedelta(seconds=i * 0.75))
    assert result["state"] == CRITICAL

    # Simulate a process restart: a brand new ZoneManager (no in-memory
    # state) rebuilding purely from what's in the DB.
    fresh_manager = ZoneManager()
    await fresh_manager.restore_from_db(db_session)

    restored = fresh_manager.get_or_create(zone.id)
    assert restored.current_state == CRITICAL
    assert restored.critical_entered_at is not None
    assert restored.critical_entered_at.tzinfo is not None
    assert restored.last_ts_server.tzinfo is not None
    assert restored.last_applied_ts_device.tzinfo is not None

    # Using the restored state with a fresh tz-aware `now` must not raise -
    # this is exactly what a live request hits immediately after restart.
    now_after_restart = dt.datetime.now(dt.timezone.utc)
    new_state = classify(restored.current_risk_score, restored.current_state, restored.critical_entered_at, now_after_restart)
    assert new_state == CRITICAL  # hasn't held long enough below threshold to exit yet
    assert restored.is_offline(now_after_restart, offline_after_seconds=3.0) is True  # T0 is in 2026, long past
