import datetime as dt

from fastapi.testclient import TestClient

from backend.main import app
from backend.pipeline import manager
from backend.priority import compute_priority_queue
from backend.zone_manager import ZoneRuntime

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


# ---------- ZoneRuntime camera cross-check ----------


def test_camera_view_none_before_any_report():
    runtime = ZoneRuntime(zone_id=1)
    assert runtime.camera_view(T0) is None


def test_camera_view_reports_agreement_with_pir():
    runtime = ZoneRuntime(zone_id=1)
    runtime.occupancy.stable_value = 1
    runtime.update_camera(True, 0.9, T0)
    view = runtime.camera_view(T0)
    assert view["occupied"] is True
    assert view["fresh"] is True
    assert view["agrees_with_pir"] is True


def test_camera_view_flags_disagreement():
    runtime = ZoneRuntime(zone_id=1)
    runtime.occupancy.stable_value = 0
    runtime.update_camera(True, 0.9, T0)
    assert runtime.camera_view(T0)["agrees_with_pir"] is False


def test_camera_goes_stale_after_threshold():
    runtime = ZoneRuntime(zone_id=1)
    runtime.update_camera(True, 0.9, T0)
    later = T0 + dt.timedelta(seconds=runtime.CAMERA_STALE_SECONDS + 1)
    view = runtime.camera_view(later)
    assert view["fresh"] is False
    assert view["agrees_with_pir"] is None  # can't compare against a stale reading


def test_effective_occupied_prefers_pir():
    runtime = ZoneRuntime(zone_id=1)
    runtime.occupancy.stable_value = 1
    runtime.update_camera(False, 0.9, T0)  # camera disagrees, PIR wins
    occupied, source = runtime.effective_occupied(T0)
    assert (occupied, source) == (True, "pir")


def test_effective_occupied_camera_rescues_dead_pir():
    runtime = ZoneRuntime(zone_id=1)
    runtime.occupancy.stable_value = 0  # PIR says empty (could be dead/blocked)
    runtime.update_camera(True, 0.9, T0)
    occupied, source = runtime.effective_occupied(T0)
    assert (occupied, source) == (True, "camera")


def test_effective_occupied_stale_camera_not_trusted():
    runtime = ZoneRuntime(zone_id=1)
    runtime.occupancy.stable_value = 0
    runtime.update_camera(True, 0.9, T0)
    later = T0 + dt.timedelta(seconds=runtime.CAMERA_STALE_SECONDS + 1)
    occupied, source = runtime.effective_occupied(later)
    assert (occupied, source) == (False, "none")


def test_effective_occupied_never_touches_risk_score():
    # The risk formula call site (backend/fusion.py) takes occupancy_factor
    # directly from OccupancyTracker.update() - effective_occupied() is a
    # priority.py-only concept and fusion.py has no reference to it.
    from pathlib import Path

    source = Path("backend/fusion.py").read_text()
    assert "effective_occupied" not in source
    assert "camera" not in source.lower()


# ---------- priority.py integration ----------


async def test_priority_queue_uses_camera_as_pir_backup(db_session, seeded):
    zone = seeded["zone"]
    runtime = manager.get_or_create(zone.id)
    runtime.current_state = "CRITICAL"
    runtime.current_risk_score = 70.0
    runtime.critical_entered_at = T0
    runtime.occupancy.stable_value = 0  # PIR: empty
    runtime.update_camera(True, 0.9, T0)  # camera: occupied

    entries = await compute_priority_queue(db_session, manager, now=T0)
    assert entries[0]["occupied"] is True
    assert entries[0]["priority"] == 85.0  # 70 + 15 occupancy bonus
    assert "camera" in entries[0]["justification"]


# ---------- HTTP endpoint ----------


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_camera_checkin_requires_zone_key(seeded):
    with TestClient(app) as client:
        resp = client.post(
            "/api/camera",
            json={"zone_id": seeded["zone"].id, "occupied": True, "confidence": 0.9, "ts_device": T0.isoformat()},
        )
        assert resp.status_code == 401


def test_camera_checkin_rejects_mismatched_zone_id(seeded):
    with TestClient(app) as client:
        resp = client.post(
            "/api/camera",
            headers={"X-Zone-Key": seeded["zone"].api_key},
            json={"zone_id": 999999, "occupied": True, "confidence": 0.9, "ts_device": T0.isoformat()},
        )
        assert resp.status_code == 403


def test_camera_checkin_success_reports_pir_agreement(seeded):
    with TestClient(app) as client:
        resp = client.post(
            "/api/camera",
            headers={"X-Zone-Key": seeded["zone"].api_key},
            json={
                "zone_id": seeded["zone"].id,
                "occupied": True,
                "confidence": 0.85,
                "ts_device": T0.isoformat(),
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["camera_occupied"] is True
        assert "pir_occupied" in body
        assert "agrees_with_pir" in body


def test_camera_appears_in_zones_status_snapshot(seeded):
    with TestClient(app) as client:
        client.post(
            "/api/camera",
            headers={"X-Zone-Key": seeded["zone"].api_key},
            json={
                "zone_id": seeded["zone"].id,
                "occupied": False,
                "confidence": 0.7,
                "ts_device": T0.isoformat(),
            },
        )
        token = _login(client, "staff1", "staff123")
        resp = client.get("/api/zones/status", headers={"Authorization": f"Bearer {token}"})
        zview = resp.json()["zones"][0]
        assert zview["camera"] is not None
        assert zview["camera"]["occupied"] is False
