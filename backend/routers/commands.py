import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Zone, ZoneTransition
from backend.schemas import CommandOut
from backend.security import get_current_zone
from backend.zone_manager import CRITICAL, SAFE, WARNING

router = APIRouter(prefix="/api/commands", tags=["commands"])

_LED_FOR_STATE = {SAFE: "green", WARNING: "yellow", CRITICAL: "red"}


@router.get("/{zone_id}", response_model=CommandOut)
async def get_command(
    zone_id: int,
    zone: Zone = Depends(get_current_zone),
    db: AsyncSession = Depends(get_db),
):
    # A zone node may only poll commands for itself.
    if zone.id != zone_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Zone key does not match zone_id")

    last_transition = (
        await db.execute(
            select(ZoneTransition)
            .where(ZoneTransition.zone_id == zone_id)
            .order_by(ZoneTransition.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if last_transition is None:
        return CommandOut(
            zone_id=zone_id,
            state=SAFE,
            buzzer=False,
            relay=False,
            led=_LED_FOR_STATE[SAFE],
            ts=dt.datetime.now(dt.timezone.utc),
            cause="sensor",
        )

    state = last_transition.to_state
    return CommandOut(
        zone_id=zone_id,
        state=state,
        buzzer=state == CRITICAL,
        relay=state == CRITICAL,
        led=_LED_FOR_STATE[state],
        ts=last_transition.ts,
        cause=last_transition.cause,
    )
