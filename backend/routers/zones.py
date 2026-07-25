import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import OFFLINE_AFTER_SECONDS
from backend.database import get_db
from backend.models import Sensor, User, Zone
from backend.pipeline import manager
from backend.priority import compute_priority_queue
from backend.schemas import PriorityEntryOut, SensorStatusOut, TrendOut, ZoneStatusOut
from backend.security import get_current_user
from backend.trend import compute_trend

router = APIRouter(prefix="/api/zones", tags=["zones"])


@router.get("/status")
async def zones_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = dt.datetime.now(dt.timezone.utc)
    zones = (await db.execute(select(Zone))).scalars().all()

    zone_outs: list[ZoneStatusOut] = []
    for zone in zones:
        runtime = manager.get_or_create(zone.id)
        sensors = (await db.execute(select(Sensor).where(Sensor.zone_id == zone.id))).scalars().all()
        offline = runtime.is_offline(now, OFFLINE_AFTER_SECONDS)
        zone_outs.append(
            ZoneStatusOut(
                zone_id=zone.id,
                name=zone.name,
                state=runtime.current_state,
                risk_score=runtime.current_risk_score,
                offline=offline,
                sensors=[SensorStatusOut(type=s.type, status="offline" if offline else s.status) for s in sensors],
                last_reading_at=runtime.last_ts_server,
            )
        )

    priority_entries = await compute_priority_queue(db, manager, now)

    return {
        "zones": [z.model_dump() for z in zone_outs],
        "priority_queue": [PriorityEntryOut(**p).model_dump() for p in priority_entries],
        "server_time": now,
    }


@router.get("/{zone_id}/trend", response_model=TrendOut)
async def zone_trend(
    zone_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    zone = await db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")
    runtime = manager.get_or_create(zone.id)
    trend = compute_trend(runtime.recent_scores, runtime.current_state)
    return TrendOut(zone_id=zone.id, **trend)
