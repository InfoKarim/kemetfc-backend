from datetime import date

from app.development_trend import (
    DIRECTION_DECLINING,
    DIRECTION_IMPROVING,
    DIRECTION_STABLE,
    MetricTrend,
)
from app.trend_drill_matching import (
    METRIC_SCALE_BOUNDS,
    identify_training_needs,
    rescale_to_0_100,
)


def make_trend(
    metric_name: str,
    direction: str,
    current_score: float | None,
    is_plateaued: bool = False,
) -> MetricTrend:
    return MetricTrend(
        metric_name=metric_name,
        points=[],
        current_score=current_score,
        previous_score=None,
        point_change=None,
        direction=direction,
        velocity_per_30_days=None,
        is_plateaued=is_plateaued,
        is_accelerating=None,
        data_quality="good",
    )


# --- rescale_to_0_100 ------------------------------------------------


def test_rescale_returns_none_for_unknown_metric():
    assert rescale_to_0_100("yoyo_kids_distance_m", 500) is None


def test_rescale_maps_bottom_of_scale_to_zero():
    assert rescale_to_0_100("ball_mastery_sole_rolls", 1) == 0.0


def test_rescale_maps_top_of_scale_to_hundred():
    assert rescale_to_0_100("ball_mastery_sole_rolls", 5) == 100.0


def test_rescale_maps_midpoint_correctly():
    assert rescale_to_0_100("ball_mastery_sole_rolls", 3) == 50.0


def test_rescale_clamps_out_of_range_values():
    assert rescale_to_0_100("ball_mastery_sole_rolls", 0) == 0.0
    assert rescale_to_0_100("ball_mastery_sole_rolls", 10) == 100.0


def test_every_configured_metric_has_a_valid_bound():
    for metric_name, (lower, upper) in METRIC_SCALE_BOUNDS.items():
        assert upper > lower


# --- identify_training_needs -------------------------------------------


def test_improving_and_stable_metrics_are_never_flagged():
    trends = [
        make_trend("ball_mastery_sole_rolls", DIRECTION_IMPROVING, 4),
        make_trend("ball_mastery_l_turn", DIRECTION_STABLE, 3),
    ]
    rankable, unrankable = identify_training_needs(trends)
    assert rankable == []
    assert unrankable == []


def test_declining_metric_with_known_scale_becomes_rankable():
    trends = [make_trend("ball_mastery_sole_rolls", DIRECTION_DECLINING, 2)]
    rankable, unrankable = identify_training_needs(trends)

    assert len(rankable) == 1
    assert rankable[0]["attribute"] == "Ball Control"
    assert rankable[0]["score"] == rescale_to_0_100("ball_mastery_sole_rolls", 2)
    assert unrankable == []


def test_plateaued_metric_is_also_flagged():
    trends = [make_trend("ball_mastery_drag_back", DIRECTION_STABLE, 3, is_plateaued=True)]
    rankable, unrankable = identify_training_needs(trends)
    assert len(rankable) == 1


def test_declining_metric_with_no_known_scale_is_unrankable():
    trends = [make_trend("yoyo_kids_distance_m", DIRECTION_DECLINING, 450)]
    rankable, unrankable = identify_training_needs(trends)

    assert rankable == []
    assert len(unrankable) == 1
    assert unrankable[0].metric_name == "yoyo_kids_distance_m"
    assert unrankable[0].reason == "declining"
    assert unrankable[0].current_score == 450


def test_metrics_sharing_a_category_are_merged_keeping_the_worst_score():
    trends = [
        make_trend("ball_mastery_sole_rolls", DIRECTION_DECLINING, 4),  # rescaled 75
        make_trend("ball_mastery_l_turn", DIRECTION_DECLINING, 1),      # rescaled 0 (worse)
    ]
    rankable, unrankable = identify_training_needs(trends)

    assert len(rankable) == 1
    assert rankable[0]["attribute"] == "Ball Control"
    assert rankable[0]["score"] == 0.0
    assert set(rankable[0]["source_metrics"]) == {"ball_mastery_sole_rolls", "ball_mastery_l_turn"}


def test_metric_with_no_current_score_is_skipped_entirely():
    trends = [make_trend("ball_mastery_sole_rolls", DIRECTION_DECLINING, None)]
    rankable, unrankable = identify_training_needs(trends)
    assert rankable == []
    assert unrankable == []
