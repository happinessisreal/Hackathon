import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import app
from backend.models import Incident
from backend.pipeline import dominant_hazards, process_override, process_reading
from backend.schemas import IngestPayload

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _t(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def _payload(seq, ts_seconds, **overrides):
    base = dict(
        seq=seq, fire=0, gas_norm=0.0, water_norm=0.0, occupancy=0, ts_device=_t(ts_seconds), uptime_ms=60_000
    )
    base.update(overrides)
    return IngestPayload(**base)


# ---------- dominant_hazards label ----------


def test_dominant_hazards_single():
    assert dominant_hazards(1.0, 0.0, 0.0) == "fire"
    assert dominant_hazards(0.0, 0.8, 0.0) == "gas"
    assert dominant_hazards(0.0, 0.0, 0.9) == "water"


def test_dominant_hazards_compound_ordered_by_contribution():
    # fire 40 + water 25 -> both above the floor, fire first
    assert dominant_hazards(1.0, 0.0, 1.0) == "fire+water"
    # gas 20 dominates water 12.5
    assert dominant_hazards(0.0, 0.8, 0.5) == "gas+water"


def test_dominant_hazards_below_floor_falls_back_to_top_contributor():
    # all tiny: no hazard >= 10 points, still names the largest non-zero one
    assert dominant_hazards(0.0, 0.2, 0.1) == "gas"


# ---------- pipeline sets hazard on the incident ----------


async def test_incident_records_hazard_from_sensor_path(db_session, seeded):
    zone = seeded["zone"]
    for i in range(5):
        await process_reading(
            db_session, zone, _payload(i, i * 0.75, fire=1, water_norm=1.0), now=_t(i * 0.75)
        )
    incident = (
        await db_session.execute(select(Incident).where(Incident.zone_id == zone.id))
    ).scalar_one()
    assert incident.hazard == "fire+water"


async def test_override_incident_hazard_is_manual(db_session, seeded):
    zone = seeded["zone"]
    await process_override(db_session, zone, "CRITICAL", "drill", now=_t(0))
    incident = (
        await db_session.execute(select(Incident).where(Incident.zone_id == zone.id))
    ).scalar_one()
    assert incident.hazard == "manual"


# ---------- HTTP filter ----------


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_incidents_filter_by_hazard(seeded):
    with TestClient(app) as client:
        admin_token = _login(client, "admin1", "admin123")
        headers = {"Authorization": f"Bearer {admin_token}"}
        client.post(
            "/api/admin/override",
            headers=headers,
            json={"zone_id": seeded["zone"].id, "target_state": "CRITICAL", "reason": "drill"},
        )

        all_incidents = client.get("/api/incidents", headers=headers).json()
        assert len(all_incidents) == 1
        assert all_incidents[0]["hazard"] == "manual"

        manual_only = client.get("/api/incidents?hazard=manual", headers=headers).json()
        assert len(manual_only) == 1

        fire_only = client.get("/api/incidents?hazard=fire", headers=headers).json()
        assert fire_only == []
