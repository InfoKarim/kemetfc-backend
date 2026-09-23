import pytest

from app.ml_prediction import TrainingExample
from app.monte_carlo_simulation import (
    MINIMUM_RESIDUALS_FOR_SIMULATION,
    UNCERTAINTY_HIGH,
    UNCERTAINTY_INSUFFICIENT_DATA,
    UNCERTAINTY_LOW,
    UNCERTAINTY_MODERATE,
    InsufficientUncertaintyDataError,
    compute_walk_forward_residuals,
    run_simulation,
)


def examples(*days_and_scores: tuple[int, float]) -> list[TrainingExample]:
    return [TrainingExample(days_since_first=d, score=s) for d, s in days_and_scores]


def linear_series(n: int, slope: float = 1.0, intercept: float = 50.0, step_days: int = 10) -> list[TrainingExample]:
    return examples(*[(i * step_days, intercept + slope * i) for i in range(n)])


# --- compute_walk_forward_residuals ----------------------------------


def test_no_residuals_below_minimum_training_size():
    series = linear_series(4)  # < WALK_FORWARD_MINIMUM_TRAINING_SIZE (5)
    assert compute_walk_forward_residuals(series) == []


def test_residual_count_matches_points_beyond_minimum_training_size():
    series = linear_series(12)
    residuals = compute_walk_forward_residuals(series)
    assert len(residuals) == 12 - 5  # WALK_FORWARD_MINIMUM_TRAINING_SIZE


def test_residuals_near_zero_for_a_perfectly_linear_series():
    series = linear_series(12, slope=1.0)
    residuals = compute_walk_forward_residuals(series)
    assert all(abs(r) < 1.0 for r in residuals)


def test_residuals_are_deterministic():
    series = linear_series(12)
    assert compute_walk_forward_residuals(series) == compute_walk_forward_residuals(series)


def test_later_points_never_change_earlier_residuals():
    # Chronological honesty: appending future points must not change
    # residuals already computed from earlier ones (no leakage from
    # future into the past).
    base_series = linear_series(10)
    extended_series = base_series + examples((200, 999.0), (210, -999.0))

    base_residuals = compute_walk_forward_residuals(base_series)
    extended_residuals = compute_walk_forward_residuals(extended_series)

    assert extended_residuals[: len(base_residuals)] == base_residuals


# --- run_simulation -----------------------------------------------------


def test_raises_below_minimum_residuals():
    with pytest.raises(InsufficientUncertaintyDataError) as excinfo:
        run_simulation(point_prediction=70.0, residuals=[1.0, -1.0], num_simulations=100, seed=1)

    assert excinfo.value.residual_count == 2
    assert excinfo.value.minimum_required == MINIMUM_RESIDUALS_FOR_SIMULATION
    assert "Insufficient validated measurement uncertainty data" in str(excinfo.value)


def test_rejects_non_positive_simulation_count():
    with pytest.raises(ValueError, match="num_simulations must be positive"):
        run_simulation(point_prediction=70.0, residuals=[1.0, -1.0, 0.5], num_simulations=0, seed=1)


def test_deterministic_with_fixed_seed():
    residuals = [1.0, -2.0, 0.5, -0.5, 3.0]
    first = run_simulation(point_prediction=70.0, residuals=residuals, num_simulations=1000, seed=42)
    second = run_simulation(point_prediction=70.0, residuals=residuals, num_simulations=1000, seed=42)
    assert first == second


def test_different_seeds_produce_different_results_where_statistically_expected():
    # Many distinct residual values and a small sample count keep the
    # bootstrap resampling itself the dominant source of variation
    # between seeds, rather than two runs coincidentally landing on the
    # same coarse percentile bucket.
    residuals = [i * 0.37 - 5.0 for i in range(25)]
    first = run_simulation(point_prediction=70.0, residuals=residuals, num_simulations=30, seed=1)
    second = run_simulation(point_prediction=70.0, residuals=residuals, num_simulations=30, seed=2)
    values = [
        (first.p05, second.p05), (first.p10, second.p10), (first.p25, second.p25),
        (first.median, second.median), (first.p75, second.p75),
        (first.p90, second.p90), (first.p95, second.p95),
    ]
    assert any(a != b for a, b in values)


def test_zero_uncertainty_when_every_residual_is_identical():
    residuals = [0.0, 0.0, 0.0, 0.0, 0.0]
    result = run_simulation(point_prediction=70.0, residuals=residuals, num_simulations=1000, seed=1)
    assert result.p05 == result.median == result.p95 == 70.0


def test_percentiles_are_monotonically_nondecreasing():
    residuals = [5.0, -8.0, 2.0, -1.0, 6.0, -4.0, 3.0, -6.0]
    result = run_simulation(point_prediction=50.0, residuals=residuals, num_simulations=5000, seed=7)
    ordered = [result.p05, result.p10, result.p25, result.p50, result.p75, result.p90, result.p95]
    assert ordered == sorted(ordered)


def test_simulated_scores_never_go_negative():
    residuals = [-100.0, -90.0, -110.0, -95.0]
    result = run_simulation(point_prediction=10.0, residuals=residuals, num_simulations=2000, seed=3)
    assert result.p05 >= 0.0


def test_uncertainty_level_boundaries():
    def level_for(n):
        residuals = [float(i) for i in range(n)]
        return run_simulation(point_prediction=50.0, residuals=residuals, num_simulations=100, seed=1).uncertainty_level

    assert level_for(3) == UNCERTAINTY_LOW
    assert level_for(7) == UNCERTAINTY_LOW
    assert level_for(8) == UNCERTAINTY_MODERATE
    assert level_for(14) == UNCERTAINTY_MODERATE
    assert level_for(15) == UNCERTAINTY_HIGH


def test_median_is_close_to_point_prediction_when_residuals_are_symmetric():
    residuals = [5.0, -5.0, 3.0, -3.0, 1.0, -1.0]
    result = run_simulation(point_prediction=60.0, residuals=residuals, num_simulations=10000, seed=1)
    assert result.median == pytest.approx(60.0, abs=1.0)


def test_default_simulation_count_is_ten_thousand():
    from app.monte_carlo_simulation import DEFAULT_SIMULATION_COUNT
    assert DEFAULT_SIMULATION_COUNT == 10_000


def test_num_simulations_is_reported_back():
    residuals = [1.0, -1.0, 2.0]
    result = run_simulation(point_prediction=50.0, residuals=residuals, num_simulations=250, seed=1)
    assert result.num_simulations == 250
