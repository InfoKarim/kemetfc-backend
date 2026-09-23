from datetime import date

from sqlalchemy.orm import Session

from app.db_models import PlayerMetricDB


class PlayerMetricService:
    """Read side of the normalized per-metric time series (see
    PlayerMetricDB's docstring). Write side lives in
    PlayerAssessmentService._record, which is the only place rows are
    ever created — always as a direct, real projection of one
    assessment's own raw_data, never computed or backfilled here."""

    def __init__(self, db: Session):
        self.db = db

    def list_for_player(
        self,
        player_id: str,
        metric_name: str | None = None,
        category: str | None = None,
        since: date | None = None,
    ) -> list[PlayerMetricDB]:
        query = self.db.query(PlayerMetricDB).filter(
            PlayerMetricDB.player_id == player_id
        )
        if metric_name is not None:
            query = query.filter(PlayerMetricDB.metric_name == metric_name)
        if category is not None:
            query = query.filter(PlayerMetricDB.category == category)
        if since is not None:
            query = query.filter(PlayerMetricDB.recorded_at >= since)
        return query.order_by(PlayerMetricDB.recorded_at.asc()).all()

    def list_metric_names(self, player_id: str) -> list[str]:
        rows = (
            self.db.query(PlayerMetricDB.metric_name)
            .filter(PlayerMetricDB.player_id == player_id)
            .distinct()
            .all()
        )
        return sorted(name for (name,) in rows)
