from sqlalchemy.orm import Session

from app.development_trend import MetricTrend, TrendPoint, calculate_metric_trend
from app.services.player_metric_service import PlayerMetricService


# Provisional, NOT empirically validated — see development_trend.py's
# module docstring for why this module never invents its own threshold.
# Each value here is derived only from the metric's own measurement
# granularity, documented per metric so a future real repeatability study
# can replace these deliberately rather than by guessing at intent.
DEFAULT_MINIMUM_MEANINGFUL_CHANGE: dict[str, float] = {
    # Ball Mastery is a 1-5 integer coach rating — the smallest possible
    # non-noise change is one full point on that scale.
    "ball_mastery_sole_rolls": 1.0,
    "ball_mastery_inside_outside_cuts": 1.0,
    "ball_mastery_l_turn": 1.0,
    "ball_mastery_drag_back": 1.0,
    # Yo-Yo Kids distance has no published pediatric repeatability figure
    # in this codebase; 40m is a round, explicitly provisional placeholder
    # (roughly one shuttle length at the test's lower levels) pending real
    # data — not a validated measurement-error estimate.
    "yoyo_kids_distance_m": 40.0,
}


class DevelopmentTrendService:
    def __init__(self, db: Session):
        self.db = db
        self._metrics = PlayerMetricService(db)

    def get_trend(
        self,
        player_id: str,
        metric_name: str,
        *,
        minimum_meaningful_change: float | None = None,
    ) -> MetricTrend:
        threshold = minimum_meaningful_change
        if threshold is None:
            threshold = DEFAULT_MINIMUM_MEANINGFUL_CHANGE.get(metric_name)
        if threshold is None:
            raise ValueError(
                f"No minimum_meaningful_change is configured for metric "
                f"'{metric_name}', and none was supplied. Trend "
                "classification requires an explicit, defensible "
                "threshold rather than a guessed default."
            )

        rows = self._metrics.list_for_player(player_id, metric_name=metric_name)
        points = [
            TrendPoint(recorded_at=row.recorded_at, score=row.metric_score)
            for row in rows
        ]
        return calculate_metric_trend(
            metric_name, points, minimum_meaningful_change=threshold
        )

    def get_all_trends(self, player_id: str) -> list[MetricTrend]:
        """Every metric this player has at least one recorded value for
        AND that has a configured, defensible threshold. A metric without
        one is silently skipped here rather than trended with a guessed
        number — surfacing that gap is the caller's job (e.g. the future
        dashboard API listing it as "trend not yet available")."""
        names = self._metrics.list_metric_names(player_id)
        return [
            self.get_trend(player_id, name)
            for name in names
            if name in DEFAULT_MINIMUM_MEANINGFUL_CHANGE
        ]
