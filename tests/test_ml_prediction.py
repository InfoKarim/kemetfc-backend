import pytest

from app.ml_prediction import (
    MINIMUM_TRAINING_SAMPLES,
    InsufficientTrainingDataError,
    TrainingExample,
    predict_future_score,
)


def examples(*days_and_scores: tuple[int, float]) -> list[TrainingExample]:
    return [TrainingExample(days_since_first=d, score=s) for d, s in days_and_scores]


def test_raises_below_minimum_sample_count():
    too_few = examples(*[(day, 60.0) for day in range(MINIMUM_TRAINING_SAMPLES - 1)])

    with pytest.raises(InsufficientTrainingDataError) as excinfo:
        predict_future_score(too_few, target_days_since_first=30)

    assert excinfo.value.sample_count == MINIMUM_TRAINING_SAMPLES - 1
    assert excinfo.value.minimum_required == MINIMUM_TRAINING_SAMPLES
    assert "Insufficient historical data" in str(excinfo.value)


def test_succeeds_at_exactly_the_minimum_sample_count():
    exact = examples(*[(day * 10, 50.0 + day) for day in range(MINIMUM_TRAINING_SAMPLES)])

    result = predict_future_score(exact, target_days_since_first=100)

    assert result.training_sample_count == MINIMUM_TRAINING_SAMPLES
    assert result.model_name == "ridge_regression"


def test_prediction_is_deterministic():
    series = examples((0, 50), (10, 55), (20, 60), (30, 65), (40, 70))

    first = predict_future_score(series, target_days_since_first=60)
    second = predict_future_score(series, target_days_since_first=60)

    assert first == second


def test_prediction_follows_an_upward_trend():
    series = examples((0, 50), (10, 55), (20, 60), (30, 65), (40, 70))

    result = predict_future_score(series, target_days_since_first=80)

    # Perfectly linear +0.5/day series: predicting 40 days past the last
    # point should be noticeably higher than the last observed score.
    assert result.predicted_score > 70


def test_prediction_follows_a_downward_trend():
    series = examples((0, 90), (10, 85), (20, 80), (30, 75), (40, 70))

    result = predict_future_score(series, target_days_since_first=80)

    assert result.predicted_score < 70


def test_prediction_never_goes_negative():
    # A steep decline extrapolated far enough would go negative under a
    # raw linear fit — the module clips to zero since no real score in
    # this system is negative.
    series = examples((0, 10), (10, 5), (20, 0), (30, -5), (40, -10))

    result = predict_future_score(series, target_days_since_first=1000)

    assert result.predicted_score >= 0.0


def test_target_can_interpolate_within_the_training_range():
    series = examples((0, 50), (10, 60), (20, 70), (30, 80), (40, 90))

    result = predict_future_score(series, target_days_since_first=20)

    # Roughly the midpoint of a straight line from 50 to 90.
    assert 65 <= result.predicted_score <= 75
