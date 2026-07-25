from fastapi.testclient import TestClient

from backend.main import app


def test_login_success_returns_token_and_role(seeded):
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "admin1", "password": "admin123"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["role"] == "admin"
        assert len(body["token"]) > 10


def test_login_wrong_password_is_401(seeded):
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "admin1", "password": "wrong"})
        assert resp.status_code == 401


def test_login_unknown_user_is_401(seeded):
    with TestClient(app) as client:
        resp = client.post("/api/auth/login", json={"username": "nope", "password": "whatever"})
        assert resp.status_code == 401


def test_health_requires_admin_not_staff(seeded):
    with TestClient(app) as client:
        staff_token = client.post(
            "/api/auth/login", json={"username": "staff1", "password": "staff123"}
        ).json()["token"]
        resp = client.get("/api/admin/health", headers={"Authorization": f"Bearer {staff_token}"})
        assert resp.status_code == 403


def test_health_ok_for_admin(seeded):
    with TestClient(app) as client:
        admin_token = client.post(
            "/api/auth/login", json={"username": "admin1", "password": "admin123"}
        ).json()["token"]
        resp = client.get("/api/admin/health", headers={"Authorization": f"Bearer {admin_token}"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_invalid_token_is_401(seeded):
    with TestClient(app) as client:
        resp = client.get("/api/zones/status", headers={"Authorization": "Bearer garbage-token"})
        assert resp.status_code == 401
