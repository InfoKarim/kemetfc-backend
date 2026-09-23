from datetime import date, timedelta

import pytest

from app.development_trend import (
    DATA_QUALITY_GOOD,
    DATA_QUALITY_INSUFFICIENT,
    DATA_QUALITY_LIMITED,
    DIRECTION_DECLINING,
    DIRECTION_IMPROVING,
    DIRECTION_INSUFFICIENT_DATA,
    DIRECTION_STABLE,
    TrendPoint,
    calculate_direction,
    calculate_metric_trend,
    calculate_velocity_per_30_days,
    classify_data_quality,
    detect_acceleration,
    detect_plateau,
)


def points(*score_and_day_offsets: tuple[float, int], base: date = date(2026, 1, 1)) -> list[TrendPoint]:
    return [
        TrendPoint(recorded_at=base + timedelta(days=offset), score=score)
        for score, offset in score_and_day_offsets
    ]


# --- data quality -----------------------------------------------------


def test_data_quality_insufficient_with_fewer_than_two_points():
    assert classify_data_quality([]) == DATA_QUALITY_INSUFFICIENT
    assert classify_data_quality(points((60, 0))) == DATA_QUALITY_INSUFFICIENT


def test_data_quality_limited_with_exactly_two_points():
    assert classify_data_quality(points((60, 0), (65, 30))) == DATA_QUALITY_LIMITED


def test_data_quality_good_with_three_or_more_points():
    assert classify_data_quality(points((60, 0), (65, 30), (70, 60))) == DATA_QUALITY_GOOD


# --- direction ----------------------------------------------------------


def test_direction_insufficient_data_with_fewer_than_two_points():
    direction, current, previous, change = calculate_direction(
        points((60, 0)), minimum_meaningful_change=2.0
    )
    assert direction == DIRECTION_INSUFFICIENT_DATA
    assert current == 60
    assert previous is None
    assert change is None


def test_direction_improving_when_change_exceeds_threshold():
    direction, current, previous, change = calculate_direction(
        points((60, 0), (70, 30)), minimum_meaningful_change=2.0
    )
    assert direction == DIRECTION_IMPROVING
    assert current == 70
    assert previous == 60
    assert change == 10


def test_direction_declining_when_change_exceeds_threshold_negatively():
    direction, *_ = calculate_direction(
        points((70, 0), (60, 30)), minimum_meaningful_change=2.0
    )
    assert direction == DIRECTION_DECLINING


def test_direction_stable_when_change_is_within_noise_threshold():
    # A 1-point change must not be reported as decline when the
    # configured "meaningful change" is 2 points.
    direction, *_ = calculate_direction(
        points((60, 0), (61, 30)), minimum_meaningful_change=2.0
    )
    assert direction == DIRECTION_STABLE


def test_direction_uses_only_the_two_most_recent_points():
    direction, current, previous, change = calculate_direction(
        points((30, 0), (60, 30), (70, 60)), minimum_meaningful_change=2.0
    )
    assert (current, previous, change) == (70, 60, 10)
    assert direction == DIRECTION_IMPROVING


def test_direction_rejects_negative_threshold():
    with pytest.raises(ValueError):
        calculate_direction(points((60, 0), (61, 30)), minimum_meaningful_change=-1)


# --- velocity -------------------------------------------------------------


def test_velocity_none_with_fewer_than_two_points():
    assert calculate_velocity_per_30_days(points((60, 0))) is None
    assert calculate_velocity_per_30_days([]) is None


def test_velocity_two_points_matches_simple_slope():
    # +30 points over 30 days == +30 points per 30 days.
    velocity = calculate_velocity_per_30_days(points((50, 0), (80, 30)))
    assert velocity == pytest.approx(30.0)


def test_velocity_perfect_linear_series():
    # +1 point/day for 60 days == +30 points per 30 days, regardless of
    # how many points sample that same line.
    series = points(*[(60 + day, day) for day in range(0, 61, 10)])
    velocity = calculate_velocity_per_30_days(series)
    assert velocity == pytest.approx(30.0)


def test_velocity_none_when_all_points_share_the_same_date():
    assert calculate_velocity_per_30_days(points((60, 0), (65, 0))) is None


def test_velocity_is_order_independent():
    forward = points((50, 0), (60, 15), (80, 30))
    backward = list(reversed(forward))
    assert calculate_velocity_per_30_days(forward) == calculate_velocity_per_30_days(backward)


# --- plateau ----------------------------------------------------------


def test_plateau_false_with_fewer_points_than_window():
    assert detect_plateau(
        points((60, 0), (61, 30)), minimum_meaningful_change=2.0, window=3
    ) is False


def test_plateau_true_when_recent_window_range_below_threshold():
    series = points((40, 0), (60, 30), (61, 60), (60, 90))
    assert detect_plateau(series, minimum_meaningful_change=2.0, window=3) is True


def test_plateau_false_when_recent_window_range_exceeds_threshold():
    series = points((40, 0), (60, 30), (70, 60), (80, 90))
    assert detect_plateau(series, minimum_meaningful_change=2.0, window=3) is False


def test_plateau_rejects_window_below_two():
    with pytest.raises(ValueError):
        detect_plateau(points((60, 0), (61, 30)), minimum_meaningful_change=2.0, window=1)


# --- acceleration -----------------------------------------------------


def test_acceleration_none_when_either_window_has_fewer_than_two_points():
    series = points((60, 0), (65, 45))
    assert detect_acceleration(series, minimum_meaningful_change=1.0) is None


def test_acceleration_true_when_recent_slope_is_steeper():
    # Prior 30 days: +10 total (slow). Recent 30 days: +40 total (fast).
    series = points((50, 0), (60, 30), (100, 60))
    result = detect_acceleration(
        series, minimum_meaningful_change=1.0, as_of=date(2026, 1, 1) + timedelta(days=60)
    )
    assert result is True


def test_acceleration_false_when_recent_slope_is_flatter():
    # Prior 30 days: +40 total (fast). Recent 30 days: +5 total (slow).
    series = points((50, 0), (90, 30), (95, 60))
    result = detect_acceleration(
        series, minimum_meaningful_change=1.0, as_of=date(2026, 1, 1) + timedelta(days=60)
    )
    assert result is False


def test_acceleration_false_when_difference_is_within_noise_threshold():
    series = points((50, 0), (60, 30), (61, 60))
    result = detect_acceleration(
        series, minimum_meaningful_change=100.0, as_of=date(2026, 1, 1) + timedelta(days=60)
    )
    assert result is False


# --- calculate_metric_trend (composition) ------------------------------


def test_metric_trend_composes_all_fields_for_a_healthy_series():
    series = points((50, 0), (60, 30), (100, 60))
    trend = calculate_metric_trend(
        "vision",
        series,
        minimum_meaningful_change=2.0,
        as_of=date(2026, 1, 1) + timedelta(days=60),
    )
    assert trend.metric_name == "vision"
    assert trend.current_score == 100
    assert trend.previous_score == 60
    assert trend.point_change == 40
    assert trend.direction == DIRECTION_IMPROVING
    assert trend.velocity_per_30_days is not None
    assert trend.is_plateaued is False
    assert trend.is_accelerating is True
    assert trend.data_quality == DATA_QUALITY_GOOD


def test_metric_trend_insufficient_data_never_claims_a_plateau_or_direction():
    trend = calculate_metric_trend("vision", points((50, 0)), minimum_meaningful_change=2.0)
    assert trend.direction == DIRECTION_INSUFFICIENT_DATA
    assert trend.is_plateaued is False
    assert trend.data_quality == DATA_QUALITY_INSUFFICIENT


def test_metric_trend_empty_series():
    trend = calculate_metric_trend("vision", [], minimum_meaningful_change=2.0)
    assert trend.direction == DIRECTION_INSUFFICIENT_DATA
    assert trend.current_score is None
    assert trend.velocity_per_30_days is None
    assert trend.is_accelerating is None
    assert trend.data_quality == DATA_QUALITY_INSUFFICIENT
