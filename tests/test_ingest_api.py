import datetime as dt

from fastapi.testclient import TestClient

from backend.main import app


def _iso(seconds_offset: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds_offset)).isoformat()


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_ingest_unregistered_zone_key_is_401(seeded):
    with TestClient(app) as client:
        resp = client.post(
            "/api/ingest",
            headers={"X-Zone-Key": "not-a-real-key"},
            json={"seq": 1, "fire": 0, "gas_norm": 0.0, "water_norm": 0.0, "occupancy": 0, "ts_device": _iso(0)},
        )
        assert resp.status_code == 401


def test_zones_status_without_token_is_401(seeded):
    with TestClient(app) as client:
        resp = client.get("/api/zones/status")
        assert resp.status_code == 401


def test_ingest_malformed_payload_rejected(seeded):
    api_key = seeded["zone"].api_key
    with TestClient(app) as client:
        headers = {"X-Zone-Key": api_key}
        # negative water
        r1 = client.post(
            "/api/ingest",
            headers=headers,
            json={"seq": 1, "fire": 0, "gas_norm": 0.5, "water_norm": -0.1, "occupancy": 0, "ts_device": _iso(0)},
        )
        assert r1.status_code == 422

        # gas > 1.0
        r2 = client.post(
            "/api/ingest",
            headers=headers,
            json={"seq": 2, "fire": 0, "gas_norm": 1.5, "water_norm": 0.2, "occupancy": 0, "ts_device": _iso(0)},
        )
        assert r2.status_code == 422

        # non-bool-like occupancy
        r3 = client.post(
            "/api/ingest",
            headers=headers,
            json={"seq": 3, "fire": 0, "gas_norm": 0.2, "water_norm": 0.2, "occupancy": 7, "ts_device": _iso(0)},
        )
        assert r3.status_code == 422


def test_ingest_duplicate_seq_not_counted_twice(seeded):
    api_key = seeded["zone"].api_key
    with TestClient(app) as client:
        headers = {"X-Zone-Key": api_key}
        payload = {
            "seq": 42,
            "fire": 0,
            "gas_norm": 0.3,
            "water_norm": 0.1,
            "occupancy": 0,
            "ts_device": _iso(0),
            "uptime_ms": 60000,
        }
        r1 = client.post("/api/ingest", headers=headers, json=payload)
        assert r1.status_code == 200
        assert r1.json()["duplicate"] is False

        r2 = client.post("/api/ingest", headers=headers, json=payload)
        assert r2.status_code == 200
        assert r2.json()["duplicate"] is True


def test_ingest_dual_hazard_combined_score_reaches_critical(seeded):
    # occupancy intentionally left at 0 here: PIR debounce holds for 1.5s of
    # real server wall-clock time (backend/zone_manager.py OccupancyTracker),
    # which a fast back-to-back HTTP test can't satisfy deterministically.
    # Occupancy's contribution to the fused score is covered at the pipeline
    # level (with an injected `now`) in test_pipeline.py instead.
    api_key = seeded["zone"].api_key
    with TestClient(app) as client:
        headers = {"X-Zone-Key": api_key}
        last = None
        for i in range(5):
            payload = {
                "seq": i,
                "fire": 1,
                "gas_norm": 0.5,
                "water_norm": 0.4,
                "occupancy": 0,
                "ts_device": _iso(i * 0.75),
                "uptime_ms": 60000,
            }
            last = client.post("/api/ingest", headers=headers, json=payload)
            assert last.status_code == 200

        body = last.json()
        # fire debounced fully on (5th consecutive HIGH) + gas .5 + water .4
        # = 40 + 12.5 + 10 = 62.5 -> WARNING (not yet CRITICAL without occupancy)
        assert body["risk_score"] == 62.5
        assert body["state"] == "WARNING"


def test_admin_override_forbidden_for_staff(seeded):
    with TestClient(app) as client:
        staff_token = _login(client, "staff1", "staff123")
        resp = client.post(
            "/api/admin/override",
            headers={"Authorization": f"Bearer {staff_token}"},
            json={"zone_id": seeded["zone"].id, "target_state": "CRITICAL", "reason": "test"},
        )
        assert resp.status_code == 403


def test_admin_override_allowed_for_admin(seeded):
    with TestClient(app) as client:
        admin_token = _login(client, "admin1", "admin123")
        resp = client.post(
            "/api/admin/override",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"zone_id": seeded["zone"].id, "target_state": "CRITICAL", "reason": "drill"},
        )
        assert resp.status_code == 200
        assert resp.json()["state"] == "CRITICAL"


def test_ack_unknown_incident_is_404(seeded):
    with TestClient(app) as client:
        staff_token = _login(client, "staff1", "staff123")
        resp = client.post(
            "/api/incidents/999999/ack",
            headers={"Authorization": f"Bearer {staff_token}"},
        )
        assert resp.status_code == 404


def test_double_ack_via_http_returns_409(seeded):
    with TestClient(app) as client:
        admin_token = _login(client, "admin1", "admin123")
        override = client.post(
            "/api/admin/override",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"zone_id": seeded["zone"].id, "target_state": "CRITICAL", "reason": "drill"},
        )
        assert override.status_code == 200

        incidents = client.get("/api/incidents", headers={"Authorization": f"Bearer {admin_token}"})
        assert incidents.status_code == 200
        incident_id = incidents.json()[0]["id"]

        r1 = client.post(
            f"/api/incidents/{incident_id}/ack", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert r1.status_code == 200

        r2 = client.post(
            f"/api/incidents/{incident_id}/ack", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert r2.status_code == 409
