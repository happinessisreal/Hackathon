from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import User, Zone
from backend.nl_report import ReportRejected, parse_report
from backend.pipeline import manager
from backend.security import require_staff_or_admin

router = APIRouter(prefix="/api/report", tags=["report"])


class ReportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("")
async def submit_report(
    payload: ReportRequest,
    user: User = Depends(require_staff_or_admin),
    db: AsyncSession = Depends(get_db),
):
    zones = (await db.execute(select(Zone))).scalars().all()
    zones_by_name = {z.name: z.id for z in zones}

    try:
        cleaned = await parse_report(payload.text, zones_by_name)
    except ReportRejected as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reason)

    runtime = manager.get_or_create(cleaned["zone_id"])
    runtime.add_advisory_report(cleaned["severity"], cleaned["received_at"])

    return {
        "understood": {
            "zone_id": cleaned["zone_id"],
            "zone_name": cleaned["zone_name"],
            "hazard_type": cleaned["hazard_type"],
            "severity": round(cleaned["severity"], 2),
            "source": cleaned["source"],
        },
        "message": (
            f"Logged as a {cleaned['hazard_type']} report for {cleaned['zone_name']} "
            f"(severity {cleaned['severity']:.2f}, parsed via {cleaned['source']}). "
            "Advisory only: adds up to +10 to that zone's priority ranking if it's "
            "already CRITICAL from sensor data, decaying over 10 minutes. It does not "
            "change any risk score or zone state, and never triggers actuation."
        ),
    }
