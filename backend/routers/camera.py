"""Bonus 1: camera-based occupancy check-in.

The camera node (sim/camera_node.py - frame-difference motion detection on
a real webcam or video file, standing in for an ESP32-CAM on Track B)
posts a boolean occupancy verdict + confidence per zone. Stored on the
in-memory runtime only: it is a live cross-check signal for the priority
ranking, not raw sensor history (the readings table stays the locked
fire/gas/water/pir shape).

Same trust model as any zone node: authenticated by X-Zone-Key, and the
backend treats the verdict as one more raw input to cross-check - never a
state or score.
"""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.models import Zone
from backend.pipeline import manager
from backend.security import get_current_zone

router = APIRouter(prefix="/api/camera", tags=["camera"])


class CameraPayload(BaseModel):
    zone_id: int
    occupied: bool
    confidence: float = Field(ge=0.0, le=1.0)
    ts_device: dt.datetime


@router.post("")
async def camera_checkin(
    payload: CameraPayload,
    zone: Zone = Depends(get_current_zone),
):
    # A camera node may only report for its own zone (same rule as
    # /api/commands/{zone_id}).
    if zone.id != payload.zone_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zone key does not match zone_id")

    now = dt.datetime.now(dt.timezone.utc)
    runtime = manager.get_or_create(zone.id)
    runtime.update_camera(payload.occupied, payload.confidence, now)

    pir = bool(runtime.occupancy.stable_value)
    return {
        "zone_id": zone.id,
        "camera_occupied": payload.occupied,
        "pir_occupied": pir,
        "agrees_with_pir": pir == payload.occupied,
        "ts": now,
    }
