"""Bonus 2: short-term risk trend. Slope of the last up-to-8 risk scores per
zone via simple least-squares; `rising` when the slope clears a threshold
and the zone is currently in the WARNING band (a CRITICAL zone is already
the priority queue's job; a SAFE zone drifting slightly isn't yet notable).
"""

from backend.zone_manager import WARNING

RISING_SLOPE_THRESHOLD = 2.0  # risk-score points per sample


def compute_slope(scores: list[float]) -> float:
    n = len(scores)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(scores) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, scores))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_trend(scores: list[float], current_state: str) -> dict:
    slope = compute_slope(scores)
    rising = slope > RISING_SLOPE_THRESHOLD and current_state == WARNING
    return {"scores": scores, "slope": round(slope, 4), "rising": rising}
