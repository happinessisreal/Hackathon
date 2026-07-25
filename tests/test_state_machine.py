import datetime as dt

from backend.state_machine import classify
from backend.zone_manager import CRITICAL, SAFE, WARNING, FireTracker, OccupancyTracker

T0 = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _t(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


# --- classify() hysteresis ---


def test_enters_critical_at_threshold():
    assert classify(65, SAFE, None, T0) == CRITICAL
    assert classify(64.99, SAFE, None, T0) == WARNING


def test_enters_warning_band():
    assert classify(30, SAFE, None, T0) == WARNING
    assert classify(29.99, SAFE, None, T0) == SAFE


def test_critical_does_not_exit_above_55_even_if_below_65():
    entered = T0
    now = _t(10)
    assert classify(60, CRITICAL, entered, now) == CRITICAL


def test_critical_does_not_exit_before_min_hold_even_if_score_drops():
    entered = T0
    now = _t(1)  # only 1s since entry, < 3s hold
    assert classify(0, CRITICAL, entered, now) == CRITICAL


def test_critical_exits_once_below_55_and_hold_satisfied():
    entered = T0
    now = _t(3.5)
    assert classify(20, CRITICAL, entered, now) == SAFE
    assert classify(40, CRITICAL, entered, now) == WARNING


def test_rapid_flip_flood_suppressed_by_hold():
    # Score oscillates below and above 65 within the 3s hold window; state must
    # stay CRITICAL throughout (no incident flood from a flip-flopping score).
    entered = T0
    for i in range(10):
        now = _t(i * 0.25)  # up to 2.25s, still under the 3s hold
        score = 70 if i % 2 == 0 else 40
        assert classify(score, CRITICAL, entered, now) == CRITICAL


# --- FireTracker debounce + decay ---


def test_flame_flicker_below_debounce_never_triggers():
    tracker = FireTracker()
    for i in range(4):  # only 4 consecutive HIGHs, debounce needs 5
        level = tracker.update(1, _t(i * 0.75))
    assert level == 0.0
    assert tracker.debounced is False


def test_flame_sustained_triggers_after_five_consecutive():
    tracker = FireTracker()
    level = 0.0
    for i in range(5):
        level = tracker.update(1, _t(i * 0.75))
    assert level == 1.0
    assert tracker.debounced is True


def test_flame_decays_linearly_over_five_seconds_on_removal():
    tracker = FireTracker()
    for i in range(5):
        tracker.update(1, _t(i * 0.75))
    off_start = _t(3.75)
    tracker.update(0, off_start)
    assert tracker.current_level(off_start) == 1.0  # decay just started
    half = tracker.current_level(off_start + dt.timedelta(seconds=2.5))
    assert abs(half - 0.5) < 1e-9
    assert tracker.current_level(off_start + dt.timedelta(seconds=5.0)) == 0.0


# --- OccupancyTracker hold ---


def test_occupancy_flicker_under_hold_does_not_change_state():
    tracker = OccupancyTracker()
    tracker.update(1, _t(0))
    val = tracker.update(0, _t(0.5))  # reverted before 1.5s hold satisfied
    assert val == 0  # stable_value never left 0


def test_occupancy_change_commits_after_hold_satisfied():
    tracker = OccupancyTracker()
    tracker.update(1, _t(0))
    val = tracker.update(1, _t(1.6))
    assert val == 1
