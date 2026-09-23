"""Connects Step 3's real development trends to the existing, already-
working, rule-based drill recommendation engine (app/drill_ranking.py,
app/drill_recommendations.py) — Step 9 of the Player Development / ML /
Monte Carlo module.

HONESTY BOUNDARY:
- A metric becomes a "training need" only from REAL, already-computed
  trend signals (app/development_trend.py): direction == "declining" or
  is_plateaued. Never a raw score threshold — no per-metric-scale
  benchmark for "low" exists (Ball Mastery's 1-5 coach rating and Yo-Yo
  Kids' raw distance in meters are not comparable, and no validated
  cutoff exists for either).
- The existing drill-ranking engine expects weakness scores on a 0-100
  scale (see drill_recommendations.py's _normalize_weakness). Only
  metrics with an ALREADY-established, real bounded scale — today,
  Ball Mastery's 1-5 coach rating, the exact bounds
  RecordBallMasterySchema already enforces — are linearly rescaled into
  that 0-100 format. This is a scale conversion of an already-real
  number, not an invented score. A metric with no known bounded scale
  (e.g. yoyo_kids_distance_m) is reported as a flagged training need
  the drill engine cannot rank yet, never silently dropped or given
  invented bounds.
- Drill category matching reuses rank_drills()'s existing exact-match
  logic completely unchanged — this module only supplies real weakness
  rows in the same {attribute, score} shape analysis-driven
  recommendations already use, so a training plan built from trend data
  goes through the identical, already-tested ranking formula.
"""

from dataclasses import dataclass

from app.development_trend import DIRECTION_DECLINING, MetricTrend


# The only metrics with an already-established, real bounded scale
# today — the exact 1-5 range RecordBallMasterySchema already enforces
# on every Ball Mastery assessment. Extend this only when a new metric
# genuinely has a known, documented bounded scale — never guess bounds
# for an open-ended measurement like a raw distance.
METRIC_SCALE_BOUNDS: dict[str, tuple[float, float]] = {
    "ball_mastery_sole_rolls": (1, 5),
    "ball_mastery_inside_outside_cuts": (1, 5),
    "ball_mastery_l_turn": (1, 5),
    "ball_mastery_drag_back": (1, 5),
}

# Reuses "Ball Control" — the SAME category name the existing manual-
# analysis flow already uses for this skill area (app/analysis.py,
# add_assessment.html) — rather than inventing a new category the drill
# library's real category_match logic has never seen.
METRIC_TO_DRILL_CATEGORY: dict[str, str] = {
    "ball_mastery_sole_rolls": "Ball Control",
    "ball_mastery_inside_outside_cuts": "Ball Control",
    "ball_mastery_l_turn": "Ball Control",
    "ball_mastery_drag_back": "Ball Control",
}


@dataclass(frozen=True)
class UnrankedTrainingNeed:
    """A real training need (declining or plateaued metric) the drill
    engine cannot yet rank — because either the metric has no known
    bounded scale to convert into the engine's 0-100 format, or no
    drill category has been mapped for it yet. Surfaced explicitly
    rather than silently dropped."""
    metric_name: str
    reason: str  # "declining" | "plateaued"
    current_score: float


def rescale_to_0_100(metric_name: str, score: float) -> float | None:
    bounds = METRIC_SCALE_BOUNDS.get(metric_name)
    if bounds is None:
        return None
    lower, upper = bounds
    if upper <= lower:
        return None
    clamped = min(max(score, lower), upper)
    return (clamped - lower) / (upper - lower) * 100


def identify_training_needs(
    trends: list[MetricTrend],
) -> tuple[list[dict], list[UnrankedTrainingNeed]]:
    """Returns (weaknesses ready for build_drill_recommendations,
    training needs the engine cannot rank yet). Weaknesses sharing the
    same drill category are merged, keeping the LOWEST (most in need)
    rescaled score for that category — never an average, which could
    understate an individual metric's real severity."""
    best_score_by_category: dict[str, float] = {}
    reasons_by_category: dict[str, list[str]] = {}
    unrankable: list[UnrankedTrainingNeed] = []

    for trend in trends:
        if trend.direction != DIRECTION_DECLINING and not trend.is_plateaued:
            continue
        if trend.current_score is None:
            continue

        reason = "declining" if trend.direction == DIRECTION_DECLINING else "plateaued"
        rescaled = rescale_to_0_100(trend.metric_name, trend.current_score)
        category = METRIC_TO_DRILL_CATEGORY.get(trend.metric_name)

        if rescaled is None or category is None:
            unrankable.append(UnrankedTrainingNeed(
                metric_name=trend.metric_name,
                reason=reason,
                current_score=trend.current_score,
            ))
            continue

        if category not in best_score_by_category or rescaled < best_score_by_category[category]:
            best_score_by_category[category] = rescaled
        reasons_by_category.setdefault(category, []).append(trend.metric_name)

    rankable = [
        {"attribute": category, "score": score, "source_metrics": reasons_by_category[category]}
        for category, score in best_score_by_category.items()
    ]
    return rankable, unrankable
