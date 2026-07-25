from backend.fusion import risk_score


def test_all_zero():
    assert risk_score(0, 0.0, 0.0, 0) == 0


def test_full_fire_only():
    assert risk_score(1.0, None, None, 0) == 40


def test_full_gas_only():
    assert risk_score(0.0, 1.0, 0.0, 0) == 25


def test_full_water_only():
    assert risk_score(0.0, 0.0, 1.0, 0) == 25


def test_occupancy_only():
    assert risk_score(0.0, 0.0, 0.0, 1) == 10


def test_dual_hazard_combination():
    # fire debounced fully on + half gas + occupied -> 40 + 12.5 + 0 + 10
    assert risk_score(1.0, 0.5, None, 1) == 62.5


def test_all_maxed_is_100():
    assert risk_score(1.0, 1.0, 1.0, 1) == 100


def test_offline_sensor_contributes_zero_not_fabricated_safe():
    # None must not be silently treated as "safe" in a way that masks a real hazard elsewhere
    assert risk_score(1.0, None, None, 0) == 40


def test_out_of_range_inputs_are_clamped_defensively():
    assert risk_score(2.0, 1.5, -0.5, 1) == 40 + 25 + 0 + 10
