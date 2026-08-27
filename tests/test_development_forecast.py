import pytest

from app.development_forecast import (
    analyze_forecast_sensitivity,
    calibrate_forecast_assumptions,
    forecast_development,
)


def test_forecast_is_deterministic_and_returns_probability_range():
    inputs = {
        "weaknesses": [{"attribute": "Passing", "score": 55}],
        "confidence_score": 0.8,
        "weeks": 6,
        "sessions_per_week": 3,
        "simulations": 1000,
        "seed": 7,
    }

    first = forecast_development(**inputs)
    second = forecast_development(**inputs)

    assert first == second
    passing = first["forecasts"][0]
    assert passing["attribute"] == "Passing"
    assert passing["projected_score"]["p10"] <= (
        passing["projected_score"]["median"]
    ) <= passing["projected_score"]["p90"]
    assert 0 <= passing["probability_of_target"] <= 1
    assert first["method"] == "monte_carlo"
    assert "not a guarantee" in first["disclaimer"]


def test_forecast_accepts_legacy_weakness_pairs():
    result = forecast_development(
        weaknesses=[["Vision", 60]],
        confidence_score=None,
        weeks=2,
        sessions_per_week=2,
        simulations=100,
    )

    assert result["analysis_confidence"] == 0.5
    assert result["forecasts"][0]["attribute"] == "Vision"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"weeks": 0}, "weeks and sessions_per_week"),
        ({"simulations": 99}, "simulations must be at least 100"),
        ({"adherence_probability": 1.1}, "adherence_probability"),
        ({"confidence_score": 1.1}, "confidence_score"),
    ],
)
def test_forecast_rejects_invalid_inputs(overrides, message):
    values = {
        "weaknesses": [{"attribute": "Vision", "score": 50}],
        "confidence_score": 0.8,
        "weeks": 4,
        "sessions_per_week": 2,
        "simulations": 100,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        forecast_development(**values)


def test_calibrates_forecast_assumptions_with_bootstrap_intervals():
    result = calibrate_forecast_assumptions(
        [
            {"planned_sessions": 10, "completed_sessions": 8, "starting_score": 50, "ending_score": 54},
            {"planned_sessions": 10, "completed_sessions": 10, "starting_score": 60, "ending_score": 65},
            {"planned_sessions": 10, "completed_sessions": 6, "starting_score": 45, "ending_score": 48},
        ],
        bootstrap_samples=100,
        seed=7,
    )

    assert result["estimates"]["adherence_probability"] == 0.8
    assert result["estimates"]["expected_gain_per_session"] == 0.5
    assert result["bootstrap_90_percent_intervals"]["adherence_probability"]["p05"] <= 0.8
    assert "not causal" in result["warning"]


def test_forecast_sensitivity_exposes_assumption_dependent_range():
    result = analyze_forecast_sensitivity(
        weaknesses=[{"attribute": "Passing", "score": 55}],
        confidence_score=0.8,
        weeks=4,
        sessions_per_week=2,
        simulations=100,
        seed=7,
    )

    assert result["scenario_count"] == 9
    passing = result["sensitivity"]["Passing"]
    assert passing["projected_median_range"][0] <= passing["projected_median_range"][1]
    assert passing["probability_of_target_range"][0] <= passing["probability_of_target_range"][1]


def test_forecast_calibration_requires_enough_observations():
    with pytest.raises(ValueError, match="three intervention"):
        calibrate_forecast_assumptions([], bootstrap_samples=100)
