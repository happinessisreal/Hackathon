"""Risk fusion formula. Locked — see CLAUDE.md. Backend-only; zone nodes never
compute a score or state, only report normalized sensor readings.

    risk_score(zone) =
        40 * fire_signal        (0..1 continuous during debounce/decay)
      + 25 * gas_level_norm     (0.0-1.0, 0 while a hazard sensor is offline)
      + 25 * water_level_norm   (0.0-1.0)
      + 10 * occupancy_factor   (0 or 1)

Fire weighted highest (fastest-escalating, most destructive in electronics
labs). Water raised to parity with gas because two of our three zones are
server/GPU rooms where a condensate leak is the realistic catastrophic
hazard. Occupancy weighted lowest in a zone's own score - an empty zone with
a real fire is still an emergency for equipment and responders - but
occupancy is weighted heavily in inter-zone *priority* ranking, where it
belongs (life > assets). See backend/priority.py.
"""

from backend.config import WEIGHT_FIRE, WEIGHT_GAS, WEIGHT_OCCUPANCY, WEIGHT_WATER


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def risk_score(fire_level: float, gas_norm: float | None, water_norm: float | None, occupancy_factor: int) -> float:
    """Pure function. `fire_level` is the debounced/decaying 0..1 contribution
    level (see backend/state.py), not the raw digital reading. `gas_norm` /
    `water_norm` of None (sensor offline, or gas still in warm-up) contribute 0 -
    callers are responsible for surfacing the OFFLINE badge separately; this
    function never fabricates a SAFE reading, it just can't score what it wasn't
    given.
    """
    fire_level = clamp01(fire_level)
    gas = clamp01(gas_norm) if gas_norm is not None else 0.0
    water = clamp01(water_norm) if water_norm is not None else 0.0
    occ = 1 if occupancy_factor else 0

    score = WEIGHT_FIRE * fire_level + WEIGHT_GAS * gas + WEIGHT_WATER * water + WEIGHT_OCCUPANCY * occ
    return round(score, 4)
