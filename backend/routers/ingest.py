from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Zone
from backend.pipeline import process_reading
from backend.schemas import IngestPayload, IngestResponse
from backend.security import get_current_zone

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("", response_model=IngestResponse)
async def ingest(
    payload: IngestPayload,
    zone: Zone = Depends(get_current_zone),
    db: AsyncSession = Depends(get_db),
):
    result = await process_reading(db, zone, payload)
    return IngestResponse(
        duplicate=result["duplicate"],
        anomaly=result.get("anomaly", False),
        state=result["state"],
        risk_score=result["risk_score"],
    )
