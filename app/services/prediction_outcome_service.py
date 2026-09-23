from datetime import date

from sqlalchemy.orm import Session

from app.db_models import MLPredictionDB, PlayerMetricDB, PredictionOutcomeDB
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id


# How close a real PlayerMetricDB reading must land to a prediction's
# target_date to count as "the actual outcome" for that forecast — an
# explicit, documented, pragmatic tolerance (not a validated figure):
# assessments are not scheduled to the day, so requiring an exact date
# match would make reconciliation almost never fire in practice.
OUTCOME_MATCH_WINDOW_DAYS = 14


class PredictionOutcomeService:
    """Step 8: reconciles predictions whose forecast horizon has passed
    against real, later PlayerMetricDB readings — never fabricates an
    outcome. A prediction with no matching real reading within the
    window simply stays unreconciled (no row), reported as such by the
    caller rather than guessed at.
    """

    def __init__(self, db: Session):
        self.db = db

    def _find_actual_reading(
        self,
        prediction: MLPredictionDB,
    ) -> PlayerMetricDB | None:
        candidates = (
            self.db.query(PlayerMetricDB)
            .filter(
                PlayerMetricDB.player_id == prediction.player_id,
                PlayerMetricDB.metric_name == prediction.metric_name,
                # Must be a genuinely NEW reading recorded after the
                # prediction was made — never the same reading the
                # prediction's own current_score/training data came
                # from, which would make "actual vs predicted" compare
                # a forecast against data it already knew.
                PlayerMetricDB.recorded_at > prediction.prediction_date,
            )
            .all()
        )
        within_window = [
            row for row in candidates
            if abs((row.recorded_at - prediction.target_date).days) <= OUTCOME_MATCH_WINDOW_DAYS
        ]
        if not within_window:
            return None
        return min(
            within_window,
            key=lambda row: abs((row.recorded_at - prediction.target_date).days),
        )

    def reconcile(self, player_id: str, metric_name: str | None = None) -> int:
        """Creates any newly-due outcomes; returns how many were
        created. Idempotent — a prediction that already has an outcome,
        or whose forecast horizon hasn't arrived yet, or that has no
        matching real reading, is left untouched on every call."""
        today = date.today()
        query = self.db.query(MLPredictionDB).filter(MLPredictionDB.player_id == player_id)
        if metric_name is not None:
            query = query.filter(MLPredictionDB.metric_name == metric_name)

        created_count = 0
        for prediction in query.all():
            if prediction.target_date > today:
                continue

            already_reconciled = (
                self.db.query(PredictionOutcomeDB)
                .filter(PredictionOutcomeDB.prediction_id == prediction.prediction_id)
                .first()
            )
            if already_reconciled is not None:
                continue

            actual_row = self._find_actual_reading(prediction)
            if actual_row is None:
                continue

            error = actual_row.metric_score - prediction.predicted_score
            self.db.add(PredictionOutcomeDB(
                outcome_id=next_entity_id(self.db, "prediction_outcome"),
                prediction_id=prediction.prediction_id,
                player_id=player_id,
                metric_name=prediction.metric_name,
                actual_score=actual_row.metric_score,
                actual_date=actual_row.recorded_at,
                prediction_error=error,
                absolute_error=abs(error),
                created_at=utcnow(),
            ))
            created_count += 1

        if created_count:
            self.db.commit()
        return created_count

    def list_for_player(
        self,
        player_id: str,
        metric_name: str | None = None,
    ) -> list[PredictionOutcomeDB]:
        """Reconciles first (see reconcile()), then returns every
        outcome recorded so far — so a caller never has to invoke a
        separate endpoint just to see up-to-date results."""
        self.reconcile(player_id, metric_name=metric_name)

        query = self.db.query(PredictionOutcomeDB).filter(
            PredictionOutcomeDB.player_id == player_id
        )
        if metric_name is not None:
            query = query.filter(PredictionOutcomeDB.metric_name == metric_name)
        return query.order_by(PredictionOutcomeDB.actual_date.asc()).all()
