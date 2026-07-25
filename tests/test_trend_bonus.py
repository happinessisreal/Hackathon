from fastapi.testclient import TestClient

from backend.main import app
from backend.trend import compute_slope, compute_trend
from backend.zone_manager import CRITICAL, SAFE, WARNING


def test_compute_slope_flat_is_zero():
    assert compute_slope([50.0, 50.0, 50.0]) == 0.0


def test_compute_slope_rising_is_positive():
    assert compute_slope([10.0, 20.0, 30.0, 40.0]) > 0


def test_compute_slope_needs_at_least_two_points():
    assert compute_slope([]) == 0.0
    assert compute_slope([42.0]) == 0.0


def test_rising_flag_only_set_in_warning_band():
    steep = [10.0, 25.0, 40.0, 55.0]
    assert compute_trend(steep, WARNING)["rising"] is True
    # Same slope, but SAFE/CRITICAL zones aren't flagged - CRITICAL already
    # has the priority queue, SAFE drift isn't yet notable (backend/trend.py).
    assert compute_trend(steep, SAFE)["rising"] is False
    assert compute_trend(steep, CRITICAL)["rising"] is False


def test_rising_flag_false_when_flat():
    flat = [50.0, 51.0, 49.0, 50.0]
    assert compute_trend(flat, WARNING)["rising"] is False


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_zones_status_payload_includes_trend_field(seeded):
    with TestClient(app) as client:
        token = _login(client, "staff1", "staff123")
        resp = client.get("/api/zones/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        zones = resp.json()["zones"]
        assert len(zones) == 1
        trend = zones[0]["trend"]
        assert "scores" in trend and "slope" in trend and "rising" in trend
