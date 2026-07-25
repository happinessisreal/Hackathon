from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from backend.config import DATABASE_URL

if "sqlite" in DATABASE_URL:
    db_path = DATABASE_URL.split("///")[-1]
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

# NullPool: aiosqlite connections aren't safe to share across event loops
# (e.g. a test's pytest-asyncio loop vs. TestClient's own loop), so avoid
# SQLAlchemy pooling entirely and open a fresh connection per checkout.
engine = create_async_engine(DATABASE_URL, echo=False, poolclass=NullPool)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


async def get_db():
    async with async_session_maker() as session:
        yield session


async def ensure_additive_columns() -> None:
    """Tiny forward-only migration for columns added after a DB may already
    exist (SQLite's create_all only creates missing *tables*, it never
    alters existing ones). Safe to run every startup - each ALTER is
    guarded by a PRAGMA existence check.
    """
    additive = [
        ("incidents", "hazard", "VARCHAR"),
    ]
    from sqlalchemy import text

    async with engine.begin() as conn:
        for table, column, ddl_type in additive:
            cols = (await conn.execute(text(f"PRAGMA table_info({table})"))).fetchall()
            if cols and column not in {c[1] for c in cols}:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
