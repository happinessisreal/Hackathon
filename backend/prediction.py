"""Bonus 3 runtime: P(zone reaches CRITICAL within 120s), from ml/model.json.

Pure Python at runtime - the logistic regression trained by ml/train.py is
just `sigmoid(w . standardize(x) + b)`, so serving it needs no scikit-learn,
no numpy, no pickle. The artifact carries its own scaler, coefficients,
threshold, and validation metrics.

HARD SAFETY RULE (CLAUDE.md #4, Bonus 3e): this module is imported by
`status_service.py` ONLY - display data. It has no import path into
`pipeline.py`, `state_machine.py`, or `routers/commands.py`, so a predicted
value structurally cannot open an incident, change a zone state, or fire
the buzzer/relay. `tests/test_prediction.py` enforces this with a source
scan, so a refactor that quietly wires prediction into actuation fails CI.
"""

import datetime as dt
import json
import math
from pathlib import Path

from backend.trend import compute_slope
from backend.zone_manager import ZoneRuntime

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "model.json"

_model: dict | None = None
_model_loaded = False


def load_model() -> dict | None:
    global _model, _model_loaded
    if not _model_loaded:
        _model_loaded = True
        try:
            _model = json.loads(MODEL_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            _model = None  # no artifact -> prediction simply unavailable
    return _model


def _sigmoid(z: float) -> float:
    if z < -60:
        return 0.0
    if z > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def predict_for_runtime(runtime: ZoneRuntime, now: dt.datetime) -> dict | None:
    """Returns {"p_critical": 0..1, "likely": bool, "model": ...} or None
    when no model artifact is present or the zone has too little history
    (< 8 scores - the slope features would be meaningless)."""
    model = load_model()
    if model is None or len(runtime.recent_scores) < 8:
        return None

    gas = runtime.last_raw.get("gas_norm")
    water = runtime.last_raw.get("water_norm")
    features = {
        "fire_level": runtime.fire.current_level(now),
        "gas_norm": gas if gas is not None else 0.0,
        "water_norm": water if water is not None else 0.0,
        "occupancy": float(runtime.occupancy.stable_value),
        # Runtime keeps only the score window (recent_scores); per-channel
        # slopes are approximated from it plus current levels at training
        # parity via the same compute_slope. For gas/water we track the
        # recent raw values on the runtime (recent_gas/recent_water).
        "gas_slope": compute_slope(runtime.recent_gas[-8:]),
        "water_slope": compute_slope(runtime.recent_water[-8:]),
        "score_slope": compute_slope(runtime.recent_scores[-8:]),
        "risk_score": runtime.current_risk_score,
    }

    z = model["intercept"]
    for name, mean, scale, coef in zip(model["features"], model["scaler_mean"], model["scaler_scale"], model["coef"]):
        value = features.get(name, 0.0)
        z += coef * ((value - mean) / (scale or 1.0))

    p = _sigmoid(z)
    return {
        "p_critical": round(p, 4),
        "likely": p >= model.get("metrics", {}).get("threshold", 0.5),
        "model": model.get("model", "unknown"),
        "horizon_seconds": 120,
    }
