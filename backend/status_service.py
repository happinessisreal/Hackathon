"""Builds the single canonical zones/status payload used by both the REST
endpoint and every WS push. There is exactly one code path that assembles
"current state" - the WS layer never invents its own view (CLAUDE.md rule 2:
single source of truth, no client-side derivation that can drift).
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import OFFLINE_AFTER_SECONDS
from backend.models import Incident, Sensor, Zone
from backend.pipeline import SENSOR_FIELD_MAP
from backend.pipeline import manager as zone_manager
from backend.priority import compute_priority_queue


async def build_zone_status_payload(db: AsyncSession, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    zones = (await db.execute(select(Zone))).scalars().all()

    zone_outs = []
    for zone in zones:
        runtime = zone_manager.get_or_create(zone.id)
        sensors = (await db.execute(select(Sensor).where(Sensor.zone_id == zone.id))).scalars().all()
        offline = runtime.is_offline(now, OFFLINE_AFTER_SECONDS)

        incident_status = None
        if runtime.open_incident_id is not None:
            incident = await db.get(Incident, runtime.open_incident_id)
            incident_status = incident.status if incident else None

        zone_outs.append(
            {
                "zone_id": zone.id,
                "name": zone.name,
                "state": runtime.current_state,
                "risk_score": runtime.current_risk_score,
                "offline": offline,
                "sensors": [
                    {
                        "type": s.type,
                        "status": "offline" if offline else s.status,
                        "value": runtime.last_raw.get(SENSOR_FIELD_MAP.get(s.type)),
                    }
                    for s in sensors
                ],
                "last_reading_at": runtime.last_ts_server.isoformat() if runtime.last_ts_server else None,
                "open_incident_id": runtime.open_incident_id,
                "incident_status": incident_status,
            }
        )

    priority_entries = await compute_priority_queue(db, zone_manager, now)

    return {
        "zones": zone_outs,
        "priority_queue": priority_entries,
        "server_time": now.isoformat(),
    }
