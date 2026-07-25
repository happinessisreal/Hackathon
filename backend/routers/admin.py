import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Incident, Reading, User, Zone
from backend.pipeline import manager, process_override
from backend.schemas import OverrideRequest, OverrideResponse
from backend.security import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/override", response_model=OverrideResponse)
async def override(
    payload: OverrideRequest,
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    zone = await db.get(Zone, payload.zone_id)
    if zone is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zone not found")

    result = await process_override(db, zone, payload.target_state, payload.reason)
    return OverrideResponse(zone_id=zone.id, state=result["state"], transitioned=result["transitioned"])


@router.get("/health")
async def health(
    user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = dt.datetime.now(dt.timezone.utc)
    zone_count = (await db.execute(select(func.count()).select_from(Zone))).scalar_one()
    reading_count = (await db.execute(select(func.count()).select_from(Reading))).scalar_one()
    open_incidents = (
        await db.execute(select(func.count()).select_from(Incident).where(Incident.status != "resolved"))
    ).scalar_one()

    zones_online = 0
    for zone_id in manager.all_runtimes():
        runtime = manager.get_or_create(zone_id)
        if not runtime.is_offline(now, offline_after_seconds=3.0):
            zones_online += 1

    return {
        "status": "ok",
        "server_time": now,
        "zone_count": zone_count,
        "zones_online": zones_online,
        "reading_count": reading_count,
        "open_incidents": open_incidents,
    }
