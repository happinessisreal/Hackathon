"""Shared helpers for the simulator/driver: DB access to zone API keys
(never exposed over the API by design, so the driver reads them straight
from the DB it shares with the backend), phantom zone provisioning for load
testing, auth tokens, and a consistent narration/result printer so every
scenario's stdout is usable as video narration cues.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from backend.database import async_session_maker  # noqa: E402
from backend.models import Acknowledgment, Incident, Reading, Sensor, Zone, ZoneTransition  # noqa: E402
from backend.security import generate_api_key  # noqa: E402

PHANTOM_PREFIX = "Phantom-"


def narrate(tc_id: str, text: str) -> None:
    print(f"\n[{tc_id}] {text}")


def result(tc_id: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"  -> {status} [{tc_id}]{suffix}")
    return ok


async def load_zones() -> dict[str, dict]:
    async with async_session_maker() as db:
        zones = (await db.execute(select(Zone))).scalars().all()
        return {z.name: {"id": z.id, "api_key": z.api_key} for z in zones}


async def create_phantom_zones(n: int) -> list[dict]:
    created = []
    async with async_session_maker() as db:
        for i in range(n):
            name = f"{PHANTOM_PREFIX}{i:03d}-{int(time.time())}"
            api_key = generate_api_key()
            zone = Zone(name=name, api_key=api_key)
            db.add(zone)
            await db.flush()
            for sensor_type in ("fire", "gas", "water", "pir"):
                db.add(Sensor(zone_id=zone.id, type=sensor_type, status="offline"))
            created.append({"id": zone.id, "name": name, "api_key": api_key})
        await db.commit()
    return created


async def delete_phantom_zones() -> int:
    """Best-effort cleanup after a load test. Every table with a zone_id FK
    is ON DELETE RESTRICT (readings, zone_transitions, incidents, sensors -
    see backend/models.py) - RESTRICT blocks the delete while a *row*
    exists, regardless of that row's status, so resolving an incident isn't
    enough on its own; the rows have to actually go. Deleted in dependency
    order (acknowledgments before incidents, everything before the zone).
    Real (non-phantom) zones are never touched.
    """
    async with async_session_maker() as db:
        zone_ids = (
            await db.execute(select(Zone.id).where(Zone.name.like(f"{PHANTOM_PREFIX}%")))
        ).scalars().all()
        if not zone_ids:
            return 0

        incident_ids = (
            await db.execute(select(Incident.id).where(Incident.zone_id.in_(zone_ids)))
        ).scalars().all()
        if incident_ids:
            await db.execute(delete(Acknowledgment).where(Acknowledgment.incident_id.in_(incident_ids)))
            await db.execute(delete(Incident).where(Incident.id.in_(incident_ids)))
        await db.execute(delete(ZoneTransition).where(ZoneTransition.zone_id.in_(zone_ids)))
        await db.execute(delete(Reading).where(Reading.zone_id.in_(zone_ids)))
        await db.execute(delete(Sensor).where(Sensor.zone_id.in_(zone_ids)))
        result = await db.execute(delete(Zone).where(Zone.id.in_(zone_ids)))
        await db.commit()
        return result.rowcount


async def get_token(client: httpx.AsyncClient, base_url: str, username: str, password: str) -> dict:
    resp = await client.post(f"{base_url}/api/auth/login", json={"username": username, "password": password})
    resp.raise_for_status()
    return resp.json()
