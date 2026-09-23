"""Historical trend calculations over a PlayerMetricDB time series.

Step 3 of the Player Development / ML / Monte Carlo module (see Step 2's
PlayerMetricDB for the underlying data). Pure, deterministic math over a
list of (date, score) points — no database access, no network, no
invented numbers.

HONESTY BOUNDARY: this module has no access to any validated measurement-
repeatability study (none exists yet in this codebase — see the Monte
Carlo work's "Insufficient validated measurement uncertainty data"
finding, which is the same underlying gap). It therefore never picks its
own "how big a change is real vs. noise" threshold. Every function that
needs one takes ``minimum_meaningful_change`` as a REQUIRED argument from
the caller — never a hardcoded default — so no unvalidated number is ever
silently smuggled into a trend classification. Choosing a defensible
value per metric (e.g. from the rating scale's own granularity, or later
from real repeatability data) is a product/config decision for a later
step, not this one.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True)
class TrendPoint:
    recorded_at: date
    score: float


@dataclass(frozen=True)
class MetricTrend:
    metric_name: str
    points: list[TrendPoint] = field(repr=False)
    current_score: float | None
    previous_score: float | None
    # A point difference, per the product requirement: "Do not calculate
    # a percentage change where a point difference is more meaningful."
    point_change: float | None
    direction: str  # "improving" | "stable" | "declining" | "insufficient_data"
    velocity_per_30_days: float | None
    is_plateaued: bool
    is_accelerating: bool | None  # None when not computable (see detect_acceleration)
    data_quality: str  # "good" | "limited" | "insufficient"


DIRECTION_IMPROVING = "improving"
DIRECTION_STABLE = "stable"
DIRECTION_DECLINING = "declining"
DIRECTION_INSUFFICIENT_DATA = "insufficient_data"

DATA_QUALITY_GOOD = "good"
DATA_QUALITY_LIMITED = "limited"
DATA_QUALITY_INSUFFICIENT = "insufficient"


def _sorted_points(points: list[TrendPoint]) -> list[TrendPoint]:
    return sorted(points, key=lambda p: p.recorded_at)


def classify_data_quality(points: list[TrendPoint]) -> str:
    if len(points) < 2:
        return DATA_QUALITY_INSUFFICIENT
    if len(points) == 2:
        return DATA_QUALITY_LIMITED
    return DATA_QUALITY_GOOD


def calculate_direction(
    points: list[TrendPoint],
    *,
    minimum_meaningful_change: float,
) -> tuple[str, float | None, float | None, float | None]:
    """Latest-vs-previous point comparison — the same two-point-delta
    concept already used by development_report_service.py, made explicit
    and reusable. Returns (direction, current_score, previous_score,
    point_change)."""
    if minimum_meaningful_change < 0:
        raise ValueError("minimum_meaningful_change cannot be negative")

    ordered = _sorted_points(points)
    if len(ordered) < 2:
        current = ordered[-1].score if ordered else None
        return DIRECTION_INSUFFICIENT_DATA, current, None, None

    previous, current = ordered[-2].score, ordered[-1].score
    point_change = current - previous

    if abs(point_change) < minimum_meaningful_change:
        direction = DIRECTION_STABLE
    elif point_change > 0:
        direction = DIRECTION_IMPROVING
    else:
        direction = DIRECTION_DECLINING

    return direction, current, previous, point_change


def calculate_velocity_per_30_days(points: list[TrendPoint]) -> float | None:
    """Least-squares linear regression slope over every point, expressed
    as score-change-per-30-days. None with fewer than 2 points, or when
    every point shares the same date (slope is undefined, not zero)."""
    ordered = _sorted_points(points)
    if len(ordered) < 2:
        return None

    base_date = ordered[0].recorded_at
    xs = [(p.recorded_at - base_date).days for p in ordered]
    ys = [p.score for p in ordered]

    if len(set(xs)) < 2:
        return None

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None

    slope_per_day = numerator / denominator
    return slope_per_day * 30


def detect_plateau(
    points: list[TrendPoint],
    *,
    minimum_meaningful_change: float,
    window: int = 3,
) -> bool:
    """Flags a plateau only when at least ``window`` assessments exist
    AND the score range across the most recent ``window`` of them is
    below ``minimum_meaningful_change`` — never from just two points
    (that's ordinary "stable," not a plateau claim), and never from a
    single outlier being mistaken for a trend."""
    if window < 2:
        raise ValueError("window must be at least 2")

    ordered = _sorted_points(points)
    if len(ordered) < window:
        return False

    recent = [p.score for p in ordered[-window:]]
    return (max(recent) - min(recent)) < minimum_meaningful_change


def detect_acceleration(
    points: list[TrendPoint],
    *,
    minimum_meaningful_change: float,
    as_of: date | None = None,
    window_days: int = 30,
) -> bool | None:
    """Compares the linear-regression velocity of the most recent
    ``window_days`` against the velocity of the ``window_days`` before
    that. Returns True (accelerating), False (decelerating/steady), or
    None when either window has fewer than 2 points to fit a slope from
    — an honest "not computable," never a guess. ``minimum_meaningful_change``
    gates the comparison the same way it gates direction/plateau, so a
    tiny slope difference within measurement noise is never reported as
    a real acceleration."""
    ordered = _sorted_points(points)
    if not ordered:
        return None

    reference = as_of or ordered[-1].recorded_at
    recent_cutoff = reference - timedelta(days=window_days)
    prior_cutoff = reference - timedelta(days=2 * window_days)

    # Inclusive on both ends so the boundary point is shared as the
    # hinge between the two segments — otherwise a window whose only
    # points fall exactly on window edges (a common case with regularly
    # spaced assessments) would spuriously look like "not enough data."
    recent_window = [p for p in ordered if recent_cutoff <= p.recorded_at <= reference]
    prior_window = [p for p in ordered if prior_cutoff <= p.recorded_at <= recent_cutoff]

    recent_velocity = calculate_velocity_per_30_days(recent_window)
    prior_velocity = calculate_velocity_per_30_days(prior_window)

    if recent_velocity is None or prior_velocity is None:
        return None

    if abs(recent_velocity - prior_velocity) < minimum_meaningful_change:
        return False

    return recent_velocity > prior_velocity


def calculate_metric_trend(
    metric_name: str,
    points: list[TrendPoint],
    *,
    minimum_meaningful_change: float,
    plateau_window: int = 3,
    acceleration_window_days: int = 30,
    as_of: date | None = None,
) -> MetricTrend:
    ordered = _sorted_points(points)
    direction, current, previous, point_change = calculate_direction(
        ordered, minimum_meaningful_change=minimum_meaningful_change
    )
    velocity = calculate_velocity_per_30_days(ordered)
    plateaued = (
        detect_plateau(
            ordered,
            minimum_meaningful_change=minimum_meaningful_change,
            window=plateau_window,
        )
        if direction != DIRECTION_INSUFFICIENT_DATA
        else False
    )
    accelerating = detect_acceleration(
        ordered,
        minimum_meaningful_change=minimum_meaningful_change,
        as_of=as_of,
        window_days=acceleration_window_days,
    )

    return MetricTrend(
        metric_name=metric_name,
        points=ordered,
        current_score=current,
        previous_score=previous,
        point_change=point_change,
        direction=direction,
        velocity_per_30_days=velocity,
        is_plateaued=plateaued,
        is_accelerating=accelerating,
        data_quality=classify_data_quality(ordered),
    )
