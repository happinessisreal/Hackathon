"""Ack race safety lives here so it can be exercised directly (two concurrent
DB sessions) in tests, not just through the HTTP layer. Exactly-once is
enforced by the DB unique constraint on acknowledgments.incident_id via
INSERT ... ON CONFLICT DO NOTHING + rowcount check (TC7b) - not application
locking, which wouldn't hold across processes/workers.
"""

import datetime as dt

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Acknowledgment, Incident


class IncidentNotFound(Exception):
    pass


class AlreadyAcknowledged(Exception):
    pass


async def ack_incident(db: AsyncSession, incident_id: int, user_id: int) -> dict:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFound(incident_id)

    now = dt.datetime.now(dt.timezone.utc)
    stmt = (
        sqlite_insert(Acknowledgment)
        .values(incident_id=incident_id, user_id=user_id, ts=now)
        .on_conflict_do_nothing(index_elements=["incident_id"])
    )
    result = await db.execute(stmt)

    if result.rowcount == 0:
        await db.rollback()
        raise AlreadyAcknowledged(incident_id)

    if incident.status == "open":
        incident.status = "acked"
    await db.commit()

    return {"incident_id": incident_id, "acked_by": user_id, "ts": now}
