from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db_models import MLPredictionDB, PlayerMetricDB
from app.ml_prediction import TrainingExample, predict_future_score
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id
from app.services.player_metric_service import PlayerMetricService


MODEL_SCOPE_POPULATION = "population"
MODEL_SCOPE_PLAYER_SPECIFIC = "player_specific"
VALID_MODEL_SCOPES = {MODEL_SCOPE_POPULATION, MODEL_SCOPE_PLAYER_SPECIFIC}

# age_group / position scopes are explicitly NOT implemented — doing so
# honestly would need team/position data cross-referenced per metric,
# which is a real additional feature, not a two-line addition to this
# set. Requesting either scope raises rather than silently falling back
# to population.


class NoRecordedMetricError(ValueError):
    """The player has no PlayerMetricDB history for this metric at
    all — distinct from InsufficientTrainingDataError, which means some
    history exists but not enough to fit a model. Without at least one
    reading there is no "current_score" to report, let alone a
    prediction."""

    def __init__(self, player_id: str, metric_name: str):
        self.player_id = player_id
        self.metric_name = metric_name
        super().__init__(
            f"Player {player_id} has no recorded '{metric_name}' history."
        )


class MLPredictionService:
    def __init__(self, db: Session):
        self.db = db
        self._metrics = PlayerMetricService(db)

    def _population_training_examples(self, metric_name: str) -> list[TrainingExample]:
        """Pools every player's own (days-since-THEIR-OWN-first-reading,
        score) pairs for this metric — never raw calendar dates, since
        players started being assessed at different, unrelated times.
        This is what makes it a real population model of "how this
        metric tends to move over a player's own tenure," rather than a
        meaningless fit against an arbitrary shared calendar origin."""
        rows = (
            self.db.query(PlayerMetricDB)
            .filter(PlayerMetricDB.metric_name == metric_name)
            .order_by(PlayerMetricDB.player_id, PlayerMetricDB.recorded_at.asc())
            .all()
        )

        first_date_by_player: dict[str, date] = {}
        examples: list[TrainingExample] = []
        for row in rows:
            first_date = first_date_by_player.setdefault(row.player_id, row.recorded_at)
            examples.append(TrainingExample(
                days_since_first=(row.recorded_at - first_date).days,
                score=row.metric_score,
            ))
        return examples

    def predict(
        self,
        player_id: str,
        metric_name: str,
        forecast_days: int,
        model_scope: str = MODEL_SCOPE_POPULATION,
    ) -> MLPredictionDB:
        if forecast_days <= 0:
            raise ValueError("forecast_days must be positive")
        if model_scope not in VALID_MODEL_SCOPES:
            raise ValueError(
                f"Unknown or unimplemented model_scope: {model_scope!r}. "
                f"Supported: {sorted(VALID_MODEL_SCOPES)}"
            )

        player_rows = self._metrics.list_for_player(player_id, metric_name=metric_name)
        if not player_rows:
            raise NoRecordedMetricError(player_id, metric_name)

        current_score = player_rows[-1].metric_score
        own_first_date = player_rows[0].recorded_at
        player_elapsed_days = (player_rows[-1].recorded_at - own_first_date).days
        target_days_since_first = player_elapsed_days + forecast_days

        if model_scope == MODEL_SCOPE_PLAYER_SPECIFIC:
            training_examples = [
                TrainingExample(
                    days_since_first=(row.recorded_at - own_first_date).days,
                    score=row.metric_score,
                )
                for row in player_rows
            ]
        else:
            training_examples = self._population_training_examples(metric_name)

        # Raises InsufficientTrainingDataError to the caller when the
        # chosen scope's pool is too small — never silently falls back
        # to the other scope, since that would mean returning a
        # prediction the caller didn't ask for from data they didn't
        # request.
        result = predict_future_score(training_examples, target_days_since_first)

        today = date.today()
        prediction = MLPredictionDB(
            prediction_id=next_entity_id(self.db, "ml_prediction"),
            player_id=player_id,
            metric_name=metric_name,
            model_scope=model_scope,
            current_score=current_score,
            predicted_score=result.predicted_score,
            prediction_date=today,
            target_date=today + timedelta(days=forecast_days),
            forecast_days=forecast_days,
            model_name=result.model_name,
            model_version=result.model_version,
            training_sample_count=result.training_sample_count,
            confidence_score=None,
            lower_bound=None,
            upper_bound=None,
            created_at=utcnow(),
        )
        self.db.add(prediction)
        self.db.commit()
        self.db.refresh(prediction)
        return prediction

    def list_for_player(
        self,
        player_id: str,
        metric_name: str | None = None,
    ) -> list[MLPredictionDB]:
        query = self.db.query(MLPredictionDB).filter(
            MLPredictionDB.player_id == player_id
        )
        if metric_name is not None:
            query = query.filter(MLPredictionDB.metric_name == metric_name)
        return query.order_by(MLPredictionDB.created_at.desc()).all()
