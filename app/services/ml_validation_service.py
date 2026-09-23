from sqlalchemy.orm import Session

from app.ml_validation import ValidationResult, validate_model
from app.services.training_examples import (
    player_specific_training_examples,
    population_training_examples,
)


MODEL_SCOPE_POPULATION = "population"
MODEL_SCOPE_PLAYER_SPECIFIC = "player_specific"
VALID_MODEL_SCOPES = {MODEL_SCOPE_POPULATION, MODEL_SCOPE_PLAYER_SPECIFIC}


class MLValidationService:
    """Read-only diagnostic: evaluates the SAME baseline model
    (app/ml_prediction.py) that POST /players/{id}/predictions uses,
    over the same real PlayerMetricDB history, via a genuine time-aware
    train/validation/test split (see app/ml_validation.py). Never
    persisted — this is a report generated on demand, not a stored
    entity."""

    def __init__(self, db: Session):
        self.db = db

    def validate(
        self,
        metric_name: str,
        model_scope: str = MODEL_SCOPE_POPULATION,
        player_id: str | None = None,
    ) -> ValidationResult:
        if model_scope not in VALID_MODEL_SCOPES:
            raise ValueError(
                f"Unknown or unimplemented model_scope: {model_scope!r}. "
                f"Supported: {sorted(VALID_MODEL_SCOPES)}"
            )
        if model_scope == MODEL_SCOPE_PLAYER_SPECIFIC:
            if player_id is None:
                raise ValueError(
                    "player_id is required to validate a player_specific model."
                )
            examples = player_specific_training_examples(self.db, player_id, metric_name)
        else:
            examples = population_training_examples(self.db, metric_name)

        return validate_model(examples)
