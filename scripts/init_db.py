"""Phase 0 scaffold script: creates the schema (if missing) and seeds the 3
locked zones with their sensors + API keys, plus two demo users
(staff1/admin1). Idempotent - safe to re-run; existing rows are left alone
and only missing ones are created. Prints the generated zone API keys and
demo credentials once so they can be copied into firmware/.env / Wokwi.

Usage:
    python scripts/init_db.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from backend.database import async_session_maker, engine  # noqa: E402
from backend.models import Base, Sensor, User, Zone  # noqa: E402
from backend.security import generate_api_key, generate_token, hash_password  # noqa: E402

ZONES = {
    "IoT Lab": ["fire", "gas", "water", "pir"],
    "Server Room": ["fire", "water", "pir"],
    "Data Science Lab": ["fire", "water", "pir"],
}

DEMO_USERS = [
    {"username": "staff1", "password": "staff123", "role": "staff"},
    {"username": "admin1", "password": "admin123", "role": "admin"},
]


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    printed_keys: list[tuple[str, str]] = []
    printed_users: list[tuple[str, str, str]] = []

    async with async_session_maker() as db:
        for zone_name, sensor_types in ZONES.items():
            existing = (await db.execute(select(Zone).where(Zone.name == zone_name))).scalar_one_or_none()
            if existing is not None:
                continue
            api_key = generate_api_key()
            zone = Zone(name=zone_name, api_key=api_key)
            db.add(zone)
            await db.flush()
            for sensor_type in sensor_types:
                db.add(Sensor(zone_id=zone.id, type=sensor_type, status="offline"))
            printed_keys.append((zone_name, api_key))

        for u in DEMO_USERS:
            existing = (await db.execute(select(User).where(User.username == u["username"]))).scalar_one_or_none()
            if existing is not None:
                continue
            db.add(
                User(
                    username=u["username"],
                    password_hash=hash_password(u["password"]),
                    role=u["role"],
                    token=None,
                )
            )
            printed_users.append((u["username"], u["password"], u["role"]))

        await db.commit()

    print("== SCS-RG init_db complete ==")
    if printed_keys:
        print("\nZone API keys (put in firmware/.env or Wokwi secrets, X-Zone-Key header):")
        for name, key in printed_keys:
            print(f"  {name:20s} {key}")
    else:
        print("\nZones already seeded - no new API keys generated.")

    if printed_users:
        print("\nDemo users (username / password / role):")
        for username, password, role in printed_users:
            print(f"  {username:10s} {password:12s} {role}")
    else:
        print("\nDemo users already seeded.")


if __name__ == "__main__":
    asyncio.run(main())
