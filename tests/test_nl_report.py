import datetime as dt

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.nl_report import ReportRejected, fallback_parse, parse_report, validate_and_clean
from backend.pipeline import manager
from backend.priority import compute_priority_queue

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
ZONES = {"IoT Lab": 1, "Server Room": 2, "Data Science Lab": 3}


# ---------- fallback_parse (offline path, no network/model) ----------


def test_fallback_parse_matches_zone_name_in_text():
    result = fallback_parse("there's a fire in the Server Room right now", list(ZONES))
    assert result["zone"] == "Server Room"
    assert result["hazard_type"] == "fire"


def test_fallback_parse_matches_water_keywords():
    result = fallback_parse("water leak near the racks", list(ZONES))
    assert result["hazard_type"] == "water"


def test_fallback_parse_matches_gas_keywords():
    result = fallback_parse("I smell gas fumes in IoT Lab", list(ZONES))
    assert result["hazard_type"] == "gas"
    assert result["zone"] == "IoT Lab"


def test_fallback_parse_severity_keywords():
    assert fallback_parse("a small flame, minor", list(ZONES))["severity"] == 0.3
    assert fallback_parse("a huge raging fire", list(ZONES))["severity"] == 0.9


def test_fallback_parse_defaults_when_nothing_recognized():
    result = fallback_parse("something seems off", list(ZONES))
    assert result["zone"] == "IoT Lab"  # first known zone, best-effort guess
    assert result["hazard_type"] == "fire"
    assert result["severity"] == 0.5


# ---------- validate_and_clean (deterministic gate) ----------


def test_validate_accepts_case_insensitive_zone_match():
    cleaned = validate_and_clean({"zone": "server room", "hazard_type": "water", "severity": 0.6}, ZONES)
    assert cleaned["zone_id"] == 2
    assert cleaned["zone_name"] == "Server Room"


def test_validate_rejects_unknown_zone():
    with pytest.raises(ReportRejected):
        validate_and_clean({"zone": "Mars Base", "hazard_type": "fire", "severity": 0.5}, ZONES)


def test_validate_rejects_hazard_outside_enum():
    with pytest.raises(ReportRejected):
        validate_and_clean({"zone": "IoT Lab", "hazard_type": "earthquake", "severity": 0.5}, ZONES)


def test_validate_clamps_severity_rather_than_rejecting():
    high = validate_and_clean({"zone": "IoT Lab", "hazard_type": "fire", "severity": 5.0}, ZONES)
    assert high["severity"] == 1.0
    low = validate_and_clean({"zone": "IoT Lab", "hazard_type": "fire", "severity": -3.0}, ZONES)
    assert low["severity"] == 0.0


def test_validate_rejects_non_numeric_severity():
    with pytest.raises(ReportRejected):
        validate_and_clean({"zone": "IoT Lab", "hazard_type": "fire", "severity": "very bad"}, ZONES)


# ---------- parse_report: LLM path vs fallback, and LLM output still gated ----------


async def test_parse_report_uses_fallback_when_llm_returns_none(monkeypatch):
    async def fake_call_llm(text):
        return None

    monkeypatch.setattr("backend.nl_report.call_llm", fake_call_llm)
    cleaned = await parse_report("fire in IoT Lab", ZONES)
    assert cleaned["source"] == "fallback"
    assert cleaned["zone_name"] == "IoT Lab"


async def test_parse_report_uses_llm_result_when_available(monkeypatch):
    async def fake_call_llm(text):
        return {"zone": "Data Science Lab", "hazard_type": "water", "severity": 0.8}

    monkeypatch.setattr("backend.nl_report.call_llm", fake_call_llm)
    cleaned = await parse_report("some free text", ZONES)
    assert cleaned["source"] == "llm"
    assert cleaned["zone_name"] == "Data Science Lab"
    assert cleaned["hazard_type"] == "water"
    assert cleaned["severity"] == 0.8


async def test_parse_report_rejects_hallucinated_llm_zone_same_as_fallback(monkeypatch):
    async def fake_call_llm(text):
        return {"zone": "Building Z", "hazard_type": "fire", "severity": 0.5}

    monkeypatch.setattr("backend.nl_report.call_llm", fake_call_llm)
    with pytest.raises(ReportRejected):
        await parse_report("some free text", ZONES)


# ---------- advisory boost -> priority.py ranking (never actuation) ----------


async def test_advisory_report_boosts_priority_of_critical_zone(db_session, seeded):
    zone = seeded["zone"]
    runtime = manager.get_or_create(zone.id)
    runtime.current_state = "CRITICAL"
    runtime.current_risk_score = 70.0
    runtime.critical_entered_at = T0

    before = await compute_priority_queue(db_session, manager, now=T0)
    assert before[0]["priority"] == 70.0

    runtime.add_advisory_report(1.0, T0)  # severity 1.0 -> +10 at t=0
    after = await compute_priority_queue(db_session, manager, now=T0)
    assert after[0]["priority"] == 80.0
    assert "NL report" in after[0]["justification"]


async def test_advisory_boost_decays_linearly_over_ten_minutes(db_session, seeded):
    zone = seeded["zone"]
    runtime = manager.get_or_create(zone.id)
    runtime.current_state = "CRITICAL"
    runtime.current_risk_score = 50.0
    runtime.critical_entered_at = T0
    runtime.add_advisory_report(1.0, T0)

    half_decayed = await compute_priority_queue(db_session, manager, now=T0 + dt.timedelta(seconds=300))
    assert half_decayed[0]["priority"] == pytest.approx(55.0, abs=0.01)  # 50 + 5

    fully_decayed = await compute_priority_queue(db_session, manager, now=T0 + dt.timedelta(seconds=600))
    assert fully_decayed[0]["priority"] == pytest.approx(50.0, abs=0.01)


async def test_advisory_boost_capped_at_ten(db_session, seeded):
    zone = seeded["zone"]
    runtime = manager.get_or_create(zone.id)
    runtime.current_state = "CRITICAL"
    runtime.current_risk_score = 50.0
    runtime.critical_entered_at = T0
    runtime.add_advisory_report(1.0, T0)
    runtime.add_advisory_report(1.0, T0)
    runtime.add_advisory_report(1.0, T0)  # 3x +10 would be 30 uncapped

    entries = await compute_priority_queue(db_session, manager, now=T0)
    assert entries[0]["priority"] == 60.0  # 50 + min(10, 30)


async def test_advisory_report_on_non_critical_zone_does_not_enter_queue(db_session, seeded):
    zone = seeded["zone"]
    runtime = manager.get_or_create(zone.id)
    runtime.current_state = "SAFE"
    runtime.add_advisory_report(1.0, T0)

    entries = await compute_priority_queue(db_session, manager, now=T0)
    assert entries == []


# ---------- HTTP endpoint ----------


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_report_endpoint_requires_auth(seeded):
    with TestClient(app) as client:
        resp = client.post("/api/report", json={"text": "fire somewhere"})
        assert resp.status_code == 401


def test_report_endpoint_happy_path_staff(seeded, monkeypatch):
    monkeypatch.setattr("backend.nl_report.LLM_API_KEY", "")
    with TestClient(app) as client:
        token = _login(client, "staff1", "staff123")
        resp = client.post(
            "/api/report",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": f"small water leak in {seeded['zone'].name}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["understood"]["zone_name"] == seeded["zone"].name
        assert body["understood"]["hazard_type"] == "water"
        assert "never triggers actuation" in body["message"]


def test_report_endpoint_rejects_empty_text(seeded):
    with TestClient(app) as client:
        token = _login(client, "staff1", "staff123")
        resp = client.post(
            "/api/report",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": ""},
        )
        assert resp.status_code == 422
