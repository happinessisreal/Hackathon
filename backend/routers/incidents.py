import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ack_service import AlreadyAcknowledged, IncidentNotFound
from backend.ack_service import ack_incident as ack_incident_core
from backend.database import get_db
from backend.events import bus
from backend.models import Acknowledgment, Incident, User, Zone, ZoneTransition
from backend.schemas import AckResponse, AcknowledgmentOut, IncidentOut, IncidentTimelineOut, ZoneTransitionOut
from backend.security import get_current_user, require_staff_or_admin

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


async def _to_incident_out(db: AsyncSession, incident: Incident) -> IncidentOut:
    zone = await db.get(Zone, incident.zone_id)
    ack = (
        await db.execute(select(Acknowledgment).where(Acknowledgment.incident_id == incident.id))
    ).scalar_one_or_none()
    return IncidentOut(
        id=incident.id,
        zone_id=incident.zone_id,
        zone_name=zone.name if zone else "?",
        opened_at=incident.opened_at,
        peak_risk=incident.peak_risk,
        status=incident.status,
        resolved_at=incident.resolved_at,
        hazard=incident.hazard,
        ack=AcknowledgmentOut.model_validate(ack) if ack else None,
    )


@router.get("", response_model=list[IncidentOut])
async def list_incidents(
    from_: dt.datetime | None = Query(default=None, alias="from"),
    to: dt.datetime | None = Query(default=None),
    zone: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    hazard: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Incident).order_by(Incident.opened_at.desc())
    if from_ is not None:
        stmt = stmt.where(Incident.opened_at >= from_)
    if to is not None:
        stmt = stmt.where(Incident.opened_at <= to)
    if zone is not None:
        stmt = stmt.where(Incident.zone_id == zone)
    if status_filter is not None:
        stmt = stmt.where(Incident.status == status_filter)
    if hazard is not None:
        # "fire" matches "fire" and compound labels like "fire+water".
        stmt = stmt.where(Incident.hazard.like(f"%{hazard}%"))

    incidents = (await db.execute(stmt)).scalars().all()
    return [await _to_incident_out(db, i) for i in incidents]


@router.get("/{incident_id}", response_model=IncidentTimelineOut)
async def incident_timeline(
    incident_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    transitions = (
        await db.execute(
            select(ZoneTransition)
            .where(
                ZoneTransition.zone_id == incident.zone_id,
                ZoneTransition.ts >= incident.opened_at,
            )
            .order_by(ZoneTransition.ts.asc())
        )
    ).scalars().all()
    # Timeline window ends at resolution (or "now" for a still-open incident);
    # bound it so we don't pull in a *later* unrelated incident's transitions.
    end = incident.resolved_at or dt.datetime.now(dt.timezone.utc)
    transitions = [t for t in transitions if t.ts <= end]

    return IncidentTimelineOut(
        incident=await _to_incident_out(db, incident),
        transitions=[ZoneTransitionOut.model_validate(t) for t in transitions],
    )


@router.post("/{incident_id}/ack", response_model=AckResponse)
async def ack_incident(
    incident_id: int,
    user: User = Depends(require_staff_or_admin),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await ack_incident_core(db, incident_id, user.id)
    except IncidentNotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    except AlreadyAcknowledged:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Incident already acknowledged")

    await bus.publish({"type": "incident_ack", "incident_id": incident_id})

    return AckResponse(**result)
