import datetime as dt

from sqlalchemy import select

from backend.models import Incident, Reading
from backend.pipeline import process_reading
from backend.schemas import IngestPayload

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _t(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def _payload(seq, ts_seconds, **overrides):
    base = dict(
        seq=seq,
        fire=0,
        gas_norm=0.0,
        water_norm=0.0,
        occupancy=0,
        ts_device=_t(ts_seconds),
        uptime_ms=60_000,
    )
    base.update(overrides)
    return IngestPayload(**base)


async def test_combined_score_with_occupancy_reaches_critical(db_session, seeded):
    zone = seeded["zone"]
    result = None
    for i in range(5):
        payload = _payload(i, i * 0.75, fire=1, gas_norm=0.5, water_norm=0.4, occupancy=1)
        result = await process_reading(db_session, zone, payload, now=_t(i * 0.75))

    assert result["risk_score"] == 72.5  # 40 + 12.5 + 10 + 10
    assert result["state"] == "CRITICAL"
    assert result["transitioned"] is True

    incidents = (await db_session.execute(select(Incident).where(Incident.zone_id == zone.id))).scalars().all()
    assert len(incidents) == 1
    assert incidents[0].status == "open"
    assert incidents[0].peak_risk == 72.5


async def test_duplicate_seq_is_not_counted_twice(db_session, seeded):
    zone = seeded["zone"]
    payload = _payload(7, 0.0)
    r1 = await process_reading(db_session, zone, payload, now=_t(0))
    r2 = await process_reading(db_session, zone, payload, now=_t(0.1))

    assert r1["duplicate"] is False
    assert r2["duplicate"] is True

    rows = (
        await db_session.execute(select(Reading).where(Reading.zone_id == zone.id, Reading.seq == 7))
    ).scalars().all()
    assert len(rows) == 1


async def test_out_of_order_ts_device_flagged_anomaly_and_state_unchanged(db_session, seeded):
    zone = seeded["zone"]
    r1 = await process_reading(db_session, zone, _payload(1, 10.0, fire=1), now=_t(10.0))
    state_before = r1["state"]
    score_before = r1["risk_score"]

    # seq is newer, but ts_device is earlier than the last applied reading's.
    r2 = await process_reading(db_session, zone, _payload(2, 3.0, fire=0), now=_t(10.5))

    assert r2["anomaly"] is True
    assert r2["state"] == state_before
    assert r2["risk_score"] == score_before

    row = (
        await db_session.execute(select(Reading).where(Reading.zone_id == zone.id, Reading.seq == 2))
    ).scalar_one()
    assert row.anomaly is True


async def test_incident_resolves_and_retrigger_opens_new_incident(db_session, seeded):
    zone = seeded["zone"]

    # Drive to CRITICAL: 5 consecutive fire=1 (debounce) + gas + water + occupancy.
    r = None
    for i in range(5):
        r = await process_reading(
            db_session,
            zone,
            _payload(i, i * 0.75, fire=1, gas_norm=0.5, water_norm=0.4, occupancy=1),
            now=_t(i * 0.75),
        )
    assert r["state"] == "CRITICAL"
    assert r["risk_score"] == 72.5  # critical_entered_at = t=3.0

    first_incident = (
        await db_session.execute(select(Incident).where(Incident.zone_id == zone.id))
    ).scalar_one()

    # Drop fire+gas+water to 0 at t=10 (>=3s past critical_entered_at): fire
    # starts decaying from 1.0 (not yet 0), occupancy hold hasn't flipped
    # yet either -> score = 40*1.0 + 0 + 0 + 10*1 = 50, which is < 55, so it
    # exits CRITICAL into WARNING and resolves the incident.
    r2 = await process_reading(
        db_session, zone, _payload(5, 10.0, fire=0, gas_norm=0.0, water_norm=0.0, occupancy=0), now=_t(10.0)
    )
    assert r2["state"] == "WARNING"
    assert r2["risk_score"] == 50.0

    await db_session.refresh(first_incident)
    assert first_incident.status == "resolved"
    assert first_incident.resolved_at is not None

    # Re-trigger well after fire has fully decayed (t=20, >5s past t=10):
    # 5 more consecutive fire=1 readings -> a brand new incident row.
    r3 = None
    for i, sec in enumerate([20.0, 20.75, 21.5, 22.25, 23.0], start=6):
        r3 = await process_reading(
            db_session,
            zone,
            _payload(i, sec, fire=1, gas_norm=0.5, water_norm=0.4, occupancy=1),
            now=_t(sec),
        )
    assert r3["state"] == "CRITICAL"
    assert r3["risk_score"] == 72.5

    all_incidents = (
        await db_session.execute(select(Incident).where(Incident.zone_id == zone.id))
    ).scalars().all()
    assert len(all_incidents) == 2
    assert {i.status for i in all_incidents} == {"resolved", "open"}
    assert first_incident.id in [i.id for i in all_incidents if i.status == "resolved"]
