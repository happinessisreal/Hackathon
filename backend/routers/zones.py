from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import User, Zone
from backend.pipeline import manager
from backend.schemas import TrendOut
from backend.security import get_current_user
from backend.status_service import build_zone_status_payload
from backend.trend import compute_trend

router = APIRouter(prefix="/api/zones", tags=["zones"])


@router.get("/status")
async def zones_status(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await build_zone_status_payload(db)


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
