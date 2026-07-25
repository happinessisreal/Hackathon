from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.database import async_session_maker
from backend.security import get_current_user_ws
from backend.status_service import build_zone_status_payload
from backend.ws_manager import ws_manager

router = APIRouter(tags=["ws"])


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str | None = None):
    async with async_session_maker() as db:
        user = await get_current_user_ws(token, db)
    if user is None:
        await websocket.close(code=4401)
        return

    await ws_manager.connect(websocket)
    try:
        async with async_session_maker() as db:
            snapshot = await build_zone_status_payload(db)
        await websocket.send_json({"event": "snapshot", **snapshot})

        while True:
            # Clients don't need to send anything; this just detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket)
