"""Bridges the pipeline's event bus to connected WS clients.

Two paths push updates:
1. Immediate: a `state_change` or `incident_ack` event (real, meaningful
   changes) triggers an instant broadcast.
2. Periodic (every ~1s): keeps risk-score numbers and unacked-seconds
   escalation moving for clients even between readings/acks, without
   broadcasting on every single 750ms sensor POST (which would be needless
   churn at load - see sim/driver.py --phantom 30, TC11).

Both paths broadcast the *same* canonical snapshot (status_service) - there
is no separate "delta" message format to keep in sync with it.
"""

import asyncio
import logging

from backend.database import async_session_maker
from backend.events import bus
from backend.status_service import build_zone_status_payload
from backend.ws_manager import ws_manager

logger = logging.getLogger("scsrg")

PERIODIC_INTERVAL_SECONDS = 1.0

_periodic_task: asyncio.Task | None = None


async def _broadcast_snapshot(event_name: str) -> None:
    if not ws_manager.active:
        return
    async with async_session_maker() as db:
        payload = await build_zone_status_payload(db)
    await ws_manager.broadcast({"event": event_name, **payload})


async def _on_bus_event(event: dict) -> None:
    if event.get("type") not in ("state_change", "incident_ack"):
        return
    await _broadcast_snapshot(event["type"])


async def _periodic_loop() -> None:
    while True:
        await asyncio.sleep(PERIODIC_INTERVAL_SECONDS)
        try:
            await _broadcast_snapshot("periodic_snapshot")
        except Exception:
            logger.exception("periodic WS broadcast failed")


def start() -> None:
    global _periodic_task
    bus.subscribe(_on_bus_event)
    _periodic_task = asyncio.create_task(_periodic_loop())


def stop() -> None:
    global _periodic_task
    bus.unsubscribe(_on_bus_event)
    if _periodic_task is not None:
        _periodic_task.cancel()
        _periodic_task = None
