"""Pure SAFE/WARNING/CRITICAL classification with hysteresis. Locked thresholds
in backend/config.py. Kept separate from I/O so it's trivially unit-testable.
"""

import datetime as dt

from backend.config import CRITICAL_EXIT_SCORE, CRITICAL_MIN_HOLD_SECONDS, SAFE_MAX, WARNING_MAX
from backend.zone_manager import CRITICAL, SAFE, WARNING


def band_for_score(score: float) -> str:
    if score >= WARNING_MAX:
        return CRITICAL
    if score >= SAFE_MAX:
        return WARNING
    return SAFE


def classify(
    score: float,
    current_state: str,
    critical_entered_at: dt.datetime | None,
    now: dt.datetime,
) -> str:
    """Returns the state that should be in effect given `score` and the
    zone's current state. Only CRITICAL has hysteresis: it can't be exited
    until the score drops below CRITICAL_EXIT_SCORE (55, not 65) AND at
    least CRITICAL_MIN_HOLD_SECONDS have elapsed since entry.
    """
    if current_state == CRITICAL:
        held_long_enough = (
            critical_entered_at is not None
            and (now - critical_entered_at).total_seconds() >= CRITICAL_MIN_HOLD_SECONDS
        )
        if score < CRITICAL_EXIT_SCORE and held_long_enough:
            return band_for_score(score)
        return CRITICAL

    return band_for_score(score)
