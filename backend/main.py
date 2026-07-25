import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.config import BASE_DIR
from backend.database import async_session_maker, engine
from backend.models import Base
from backend.pipeline import manager
from backend.routers import admin, auth, commands, incidents, ingest, zones

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scsrg")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Rule (CLAUDE.md #6): rebuild zone states from the DB before accepting
    # traffic. Never assume SAFE for a zone that already had an incident open.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as db:
        await manager.restore_from_db(db)

    logger.info("SCS-RG backend ready; %d zone(s) restored from DB", len(manager.all_runtimes()))
    yield
    await engine.dispose()


app = FastAPI(title="SCS-RG: Multi-Hazard Smart Campus Safety & Response Grid", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(ingest.router)
app.include_router(zones.router)
app.include_router(incidents.router)
app.include_router(admin.router)
app.include_router(commands.router)


@app.get("/api/ping", tags=["meta"])
async def ping():
    return {"status": "ok"}


frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
