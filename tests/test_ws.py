import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import app


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_ws_rejects_missing_token(seeded):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass


def test_ws_rejects_invalid_token(seeded):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?token=garbage"):
                pass


def test_ws_sends_snapshot_on_connect(seeded):
    with TestClient(app) as client:
        token = _login(client, "admin1", "admin123")
        with client.websocket_connect(f"/ws?token={token}") as ws:
            msg = ws.receive_json()
            assert msg["event"] == "snapshot"
            assert "zones" in msg
            assert "priority_queue" in msg
            assert any(z["zone_id"] == seeded["zone"].id for z in msg["zones"])


class _FakeWebSocket:
    """Stands in for a connected client in ws_manager.active. Deliberately
    NOT a real network socket / TestClient websocket: a real one lives on a
    separate thread with its own event loop, and backend/zone_manager.py's
    per-zone asyncio.Lock is bound to whichever loop first creates it -
    using it from a second loop (e.g. a second TestClient) deadlocks. A
    single uvicorn worker only ever has one loop, so that's a testing
    artifact, not a production bug - this test just avoids tripping it by
    staying on pytest-asyncio's single loop throughout.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)


async def test_broadcaster_pushes_snapshot_on_state_change(db_session, seeded):
    from backend import broadcaster
    from backend.pipeline import process_override
    from backend.ws_manager import ws_manager

    fake = _FakeWebSocket()
    ws_manager.active.add(fake)
    broadcaster.start()
    try:
        result = await process_override(db_session, seeded["zone"], "CRITICAL", "drill")
        assert result["state"] == "CRITICAL"

        assert len(fake.messages) == 1
        msg = fake.messages[0]
        assert msg["event"] == "state_change"
        zone_entry = next(z for z in msg["zones"] if z["zone_id"] == seeded["zone"].id)
        assert zone_entry["state"] == "CRITICAL"
        assert len(msg["priority_queue"]) == 1
    finally:
        broadcaster.stop()
        ws_manager.active.discard(fake)
