import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp_dir = tempfile.mkdtemp(prefix="scsrg_test_")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{Path(_tmp_dir, 'test.db').as_posix()}"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from backend.database import Base, async_session_maker, engine  # noqa: E402
from backend.models import Sensor, User, Zone  # noqa: E402
from backend.pipeline import manager as zone_manager  # noqa: E402
from backend.security import generate_api_key, hash_password  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _reset_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    zone_manager._runtimes.clear()
    zone_manager._locks.clear()
    yield


@pytest_asyncio.fixture
async def db_session():
    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def seeded(db_session):
    """One zone with all 4 sensor types + staff1/admin1 users."""
    zone = Zone(name="Test Zone", api_key=generate_api_key())
    db_session.add(zone)
    await db_session.flush()
    for t in ["fire", "gas", "water", "pir"]:
        db_session.add(Sensor(zone_id=zone.id, type=t, status="offline"))

    staff = User(username="staff1", password_hash=hash_password("staff123"), role="staff")
    admin = User(username="admin1", password_hash=hash_password("admin123"), role="admin")
    db_session.add_all([staff, admin])
    await db_session.commit()
    await db_session.refresh(zone)
    await db_session.refresh(staff)
    await db_session.refresh(admin)
    return {"zone": zone, "staff": staff, "admin": admin}


@pytest.fixture
def anyio_backend():
    return "asyncio"
