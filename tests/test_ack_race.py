import asyncio
import datetime as dt

import pytest

from backend.ack_service import AlreadyAcknowledged, ack_incident
from backend.database import async_session_maker
from backend.models import Incident


async def _make_open_incident(zone_id: int) -> int:
    async with async_session_maker() as db:
        incident = Incident(
            zone_id=zone_id,
            opened_at=dt.datetime.now(dt.timezone.utc),
            peak_risk=80.0,
            status="open",
        )
        db.add(incident)
        await db.commit()
        await db.refresh(incident)
        return incident.id


async def test_exactly_one_ack_wins_concurrent_race(seeded):
    incident_id = await _make_open_incident(seeded["zone"].id)

    async def attempt(user_id: int):
        async with async_session_maker() as db:
            return await ack_incident(db, incident_id, user_id)

    results = await asyncio.gather(
        attempt(seeded["staff"].id),
        attempt(seeded["admin"].id),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], AlreadyAcknowledged)

    # DB-level confirmation: exactly one acknowledgment row exists for this incident.
    async with async_session_maker() as db:
        from sqlalchemy import select

        from backend.models import Acknowledgment

        rows = (
            await db.execute(select(Acknowledgment).where(Acknowledgment.incident_id == incident_id))
        ).scalars().all()
        assert len(rows) == 1
