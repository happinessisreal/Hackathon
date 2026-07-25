"""Minimal async pub-sub so the ingestion/incident pipeline (Phase 1) can
announce events without depending on the WS layer (Phase 2). The WS
connection manager subscribes here; until it does, publishing is a no-op.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("scsrg")

Listener = Callable[[dict], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def publish(self, event: dict) -> None:
        if not self._listeners:
            return
        results = await asyncio.gather(
            *(listener(event) for listener in self._listeners), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.exception("event bus listener failed for %s", event, exc_info=result)


bus = EventBus()
