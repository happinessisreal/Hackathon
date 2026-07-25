"""Orchestrates ingestion and admin override: dedup -> anomaly check -> fusion
-> state classification -> transition/incident bookkeeping. Both entry points
serialize on a per-zone asyncio.Lock so a sensor-driven transition and an
admin override arriving "at the same instant" can never both fire actuation
for the same state entry (CLAUDE.md rule: edge-triggered, not double-fired).
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.events import bus
from backend.fusion import risk_score
from backend.models import Incident, Reading, Sensor, Zone, ZoneTransition
from backend.schemas import IngestPayload
from backend.state_machine import classify
from backend.zone_manager import CRITICAL, ZoneManager, ZoneRuntime

manager = ZoneManager()

SENSOR_FIELD_MAP = {
    "fire": "fire",
    "gas": "gas_norm",
    "water": "water_norm",
    "pir": "occupancy",
}

HAZARD_CONTRIBUTION_FLOOR = 10.0  # points; a hazard below this isn't "the" cause


def dominant_hazards(fire_level: float, gas_value: float | None, water_norm: float | None) -> str:
    """Human-readable hazard label for an incident (TC14: the log must show
    hazard type, not just a score). Every hazard contributing >= the floor
    is listed, largest first, e.g. "fire", "fire+water". Occupancy is not a
    hazard type; it never appears here."""
    from backend.config import WEIGHT_FIRE, WEIGHT_GAS, WEIGHT_WATER

    contributions = {
        "fire": WEIGHT_FIRE * fire_level,
        "gas": WEIGHT_GAS * (gas_value or 0.0),
        "water": WEIGHT_WATER * (water_norm or 0.0),
    }
    named = sorted(
        ((h, c) for h, c in contributions.items() if c >= HAZARD_CONTRIBUTION_FLOOR),
        key=lambda item: -item[1],
    )
    if not named:
        top = max(contributions, key=contributions.get)
        return top if contributions[top] > 0 else "mixed"
    return "+".join(h for h, _ in named)


async def _update_sensor_statuses(db: AsyncSession, zone_id: int, payload: IngestPayload) -> None:
    sensors = (await db.execute(select(Sensor).where(Sensor.zone_id == zone_id))).scalars().all()
    for sensor in sensors:
        field = SENSOR_FIELD_MAP.get(sensor.type)
        if field is None:
            continue
        value = getattr(payload, field)
        sensor.status = "offline" if value is None else "online"


async def _transition_to(
    db: AsyncSession,
    zone: Zone,
    runtime: ZoneRuntime,
    new_state: str,
    new_score: float,
    cause: str,
    now: dt.datetime,
    reason: str | None = None,
    hazard: str | None = None,
) -> ZoneTransition | None:
    runtime.current_risk_score = new_score

    # Keep incident peak_risk current even without a state transition.
    if runtime.current_state == CRITICAL and runtime.open_incident_id is not None:
        incident = await db.get(Incident, runtime.open_incident_id)
        if incident is not None and new_score > incident.peak_risk:
            incident.peak_risk = new_score

    if new_state == runtime.current_state:
        return None

    old_state = runtime.current_state
    transition = ZoneTransition(
        zone_id=zone.id,
        from_state=old_state,
        to_state=new_state,
        risk_score=new_score,
        cause=cause,
        reason=reason,
        ts=now,
    )
    db.add(transition)
    runtime.current_state = new_state

    if new_state == CRITICAL:
        runtime.critical_entered_at = now
    else:
        runtime.critical_entered_at = None

    if new_state == CRITICAL and old_state != CRITICAL:
        incident = Incident(zone_id=zone.id, opened_at=now, peak_risk=new_score, status="open", hazard=hazard)
        db.add(incident)
        await db.flush()
        runtime.open_incident_id = incident.id
    elif old_state == CRITICAL and new_state != CRITICAL and runtime.open_incident_id is not None:
        incident = await db.get(Incident, runtime.open_incident_id)
        if incident is not None and incident.status in ("open", "acked"):
            incident.status = "resolved"
            incident.resolved_at = now
        runtime.open_incident_id = None

    await db.flush()
    return transition


async def process_reading(
    db: AsyncSession, zone: Zone, payload: IngestPayload, now: dt.datetime | None = None
) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    runtime = manager.get_or_create(zone.id)

    async with manager.lock_for(zone.id):
        existing = (
            await db.execute(select(Reading).where(Reading.zone_id == zone.id, Reading.seq == payload.seq))
        ).scalar_one_or_none()
        if existing is not None:
            return {"duplicate": True, "state": runtime.current_state, "risk_score": runtime.current_risk_score}

        anomaly = runtime.last_applied_ts_device is not None and payload.ts_device < runtime.last_applied_ts_device

        await _update_sensor_statuses(db, zone.id, payload)

        db.add(
            Reading(
                zone_id=zone.id,
                seq=payload.seq,
                fire=payload.fire,
                gas_norm=payload.gas_norm,
                water_norm=payload.water_norm,
                occupancy=payload.occupancy,
                ts_device=payload.ts_device,
                ts_server=now,
                anomaly=anomaly,
            )
        )
        runtime.last_seq = payload.seq
        runtime.last_ts_server = now
        runtime.last_raw = {
            "fire": payload.fire,
            "gas_norm": payload.gas_norm,
            "water_norm": payload.water_norm,
            "occupancy": payload.occupancy,
        }

        if anomaly:
            await db.commit()
            return {
                "duplicate": False,
                "anomaly": True,
                "state": runtime.current_state,
                "risk_score": runtime.current_risk_score,
            }

        if payload.uptime_ms is not None:
            # Recomputed every reading, not just the first: in continuous
            # operation this stays ~constant (now and uptime_ms advance
            # together), but a real reboot (uptime_ms resets near 0) makes
            # the candidate jump forward and correctly re-arms warm-up -
            # without needing a separate explicit "reboot" signal.
            runtime.boot_ts = now - dt.timedelta(seconds=payload.uptime_ms / 1000.0)
        elif runtime.boot_ts is None:
            # No uptime signal at all: fail-safe assumption of a fresh boot,
            # applied once so it still naturally elapses after 30s instead
            # of blocking gas forever.
            runtime.boot_ts = now

        fire_level = runtime.fire.update(payload.fire, now)
        gas_value = runtime.gas_value(payload.gas_norm, now)
        occupancy_factor = runtime.occupancy.update(payload.occupancy, now)
        score = risk_score(fire_level, gas_value, payload.water_norm, occupancy_factor)

        runtime.last_applied_ts_device = payload.ts_device
        runtime.record_score(score, gas=gas_value, water=payload.water_norm)

        new_state = classify(score, runtime.current_state, runtime.critical_entered_at, now)
        transition = await _transition_to(
            db,
            zone,
            runtime,
            new_state,
            score,
            cause="sensor",
            now=now,
            hazard=dominant_hazards(fire_level, gas_value, payload.water_norm),
        )
        await db.commit()

        await bus.publish(
            {
                "type": "state_change" if transition else "reading_update",
                "zone_id": zone.id,
                "risk_score": score,
                "state": runtime.current_state,
            }
        )

        return {
            "duplicate": False,
            "anomaly": False,
            "risk_score": score,
            "state": runtime.current_state,
            "transitioned": transition is not None,
        }


async def process_override(
    db: AsyncSession, zone: Zone, target_state: str, reason: str, now: dt.datetime | None = None
) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    runtime = manager.get_or_create(zone.id)

    async with manager.lock_for(zone.id):
        transition = await _transition_to(
            db,
            zone,
            runtime,
            target_state,
            runtime.current_risk_score,
            cause="manual",
            now=now,
            reason=reason,
            hazard="manual",
        )
        await db.commit()

        await bus.publish(
            {
                "type": "state_change" if transition else "override_noop",
                "zone_id": zone.id,
                "risk_score": runtime.current_risk_score,
                "state": runtime.current_state,
            }
        )

        return {"state": runtime.current_state, "transitioned": transition is not None}
