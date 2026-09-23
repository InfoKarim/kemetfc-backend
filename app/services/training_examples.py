from datetime import date

from sqlalchemy.orm import Session

from app.db_models import PlayerMetricDB
from app.ml_prediction import TrainingExample


def population_training_examples(db: Session, metric_name: str) -> list[TrainingExample]:
    """Pools every player's own (days-since-THEIR-OWN-first-reading,
    score) pairs for this metric — never raw calendar dates, since
    players started being assessed at different, unrelated times. This
    is what makes it a real population model of "how this metric tends
    to move over a player's own tenure," rather than a meaningless fit
    against an arbitrary shared calendar origin. Shared by
    MLPredictionService and MLValidationService so both scope a
    "population" model identically."""
    rows = (
        db.query(PlayerMetricDB)
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


def player_specific_training_examples(
    db: Session, player_id: str, metric_name: str
) -> list[TrainingExample]:
    rows = (
        db.query(PlayerMetricDB)
        .filter(
            PlayerMetricDB.player_id == player_id,
            PlayerMetricDB.metric_name == metric_name,
        )
        .order_by(PlayerMetricDB.recorded_at.asc())
        .all()
    )
    if not rows:
        return []

    first_date = rows[0].recorded_at
    return [
        TrainingExample(
            days_since_first=(row.recorded_at - first_date).days,
            score=row.metric_score,
        )
        for row in rows
    ]
