"""Tracks live WebSocket connections and broadcasts JSON-serializable
messages to all of them. A dead connection is dropped on first send failure
rather than tracked separately - `disconnect()` handles both the normal
close path and this cleanup path identically.
"""

from starlette.websockets import WebSocket


class WSConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active.discard(websocket)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = WSConnectionManager()
