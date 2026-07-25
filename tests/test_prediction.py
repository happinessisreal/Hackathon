import datetime as dt
import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import prediction
from backend.main import app
from backend.pipeline import manager
from backend.prediction import _sigmoid, predict_for_runtime
from backend.zone_manager import ZoneRuntime

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)

# ---------- hard safety rule (CLAUDE.md #4 / Bonus 3e): no import path from
# prediction into anything that can actuate. A source scan, not a runtime
# check, so a future refactor that wires this in gets caught even if no
# test happens to exercise that code path. ----------

ACTUATION_MODULES = [
    Path("backend/pipeline.py"),
    Path("backend/state_machine.py"),
    Path("backend/fusion.py"),
    Path("backend/routers/commands.py"),
    Path("backend/routers/ingest.py"),
]


def test_no_actuation_module_imports_prediction():
    for path in ACTUATION_MODULES:
        source = path.read_text()
        assert "backend.prediction" not in source and "import prediction" not in source, (
            f"{path} must never import backend.prediction - predicted values "
            "cannot be allowed anywhere near actuation (CLAUDE.md rule 4)"
        )


def test_prediction_module_does_not_import_actuation_writers():
    source = Path("backend/prediction.py").read_text()
    # It may read runtime state (ZoneRuntime) but must never import the
    # pipeline functions that write transitions/incidents or send commands.
    assert "process_reading" not in source
    assert "process_override" not in source
    assert "routers.commands" not in source


# ---------- math ----------


def test_sigmoid_bounds_and_midpoint():
    assert _sigmoid(0.0) == 0.5
    assert _sigmoid(100.0) == 1.0
    assert _sigmoid(-100.0) == 0.0
    assert 0.99 < _sigmoid(10.0) < 1.0


# ---------- predict_for_runtime ----------


def test_predict_returns_none_without_enough_history():
    runtime = ZoneRuntime(zone_id=1)
    runtime.recent_scores = [10.0, 20.0]  # < 8 samples
    assert predict_for_runtime(runtime, T0) is None


def test_predict_returns_none_when_model_artifact_missing(monkeypatch):
    monkeypatch.setattr(prediction, "_model", None)
    monkeypatch.setattr(prediction, "_model_loaded", True)
    runtime = ZoneRuntime(zone_id=1)
    runtime.recent_scores = [10.0] * 8
    assert predict_for_runtime(runtime, T0) is None


def test_predict_returns_probability_with_real_artifact():
    assert Path("ml/model.json").exists(), "run `python ml/train.py` to generate ml/model.json"
    prediction._model = None
    prediction._model_loaded = False

    runtime = ZoneRuntime(zone_id=1)
    runtime.recent_scores = [10.0, 12.0, 15.0, 20.0, 28.0, 35.0, 42.0, 50.0]
    runtime.recent_gas = [0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6]
    runtime.recent_water = [0.0] * 8
    runtime.current_risk_score = 50.0
    runtime.occupancy.stable_value = 1

    out = predict_for_runtime(runtime, T0)
    assert out is not None
    assert 0.0 <= out["p_critical"] <= 1.0
    assert isinstance(out["likely"], bool)
    assert out["horizon_seconds"] == 120


def test_model_artifact_reports_honest_metrics():
    model = json.loads(Path("ml/model.json").read_text())
    assert "synthetic" in model["data"].lower()
    for key in ("accuracy", "precision", "recall", "threshold"):
        assert key in model["metrics"]
        assert 0.0 <= model["metrics"][key] <= 1.0


# ---------- embedded in the canonical snapshot, never blended with risk_score ----------


def _login(client: TestClient, username: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_zones_status_predicted_risk_is_separate_field(seeded):
    zone = seeded["zone"]
    runtime = manager.get_or_create(zone.id)
    runtime.recent_scores = [10.0, 12.0, 15.0, 20.0, 28.0, 35.0, 42.0, 50.0]
    runtime.recent_gas = [0.1, 0.2, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6]
    runtime.recent_water = [0.0] * 8
    runtime.current_risk_score = 50.0

    with TestClient(app) as client:
        token = _login(client, "staff1", "staff123")
        resp = client.get("/api/zones/status", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        zview = resp.json()["zones"][0]
        assert "predicted_risk" in zview
        assert "risk_score" in zview
        # never blended: risk_score is the fused number, predicted_risk (if
        # present) is a nested dict under its own key.
        assert not isinstance(zview["risk_score"], dict)
