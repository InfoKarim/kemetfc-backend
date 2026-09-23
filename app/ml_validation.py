"""Time-aware validation for the baseline ML model (Step 6 of the Player
Development / ML / Monte Carlo module — see app/ml_prediction.py for the
model this validates).

HONESTY BOUNDARY:
- Splits chronologically (train = earliest points, validation = next,
  test = latest) — never shuffled. A random split would let a model
  "predict" using points that occurred AFTER what it's being scored
  against, which is real data leakage for a time series, not a
  theoretical concern.
- Reports MAE, RMSE, and R² as raw numbers only. This module never
  computes a "the model is good" verdict from them — the product
  requirement is explicit that a high R² alone must not be treated as
  proof of quality, and with realistically 10-20 samples per metric,
  R² on a 2-4 point test split is barely informative on its own anyway.
  A human reads the numbers alongside sample_count and overfitting_
  detected; this module does not pre-judge for them.
- Refuses to attempt a 3-way split below MINIMUM_SAMPLES_FOR_VALIDATION,
  raising InsufficientValidationDataError. That floor (and the
  train/validation/test fractions, and the overfitting-ratio heuristic)
  are pragmatic, documented choices — none of them come from a
  validated statistical study, because none exists in this codebase.
"""

from dataclasses import dataclass, field

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from app.ml_prediction import MODEL_NAME, MODEL_VERSION, TrainingExample


# A time series this short cannot support a meaningful chronological
# train/validation/test split at all — below this, Step 6 does not
# attempt one. Not an empirically validated minimum (see module
# docstring); chosen so a 60/20/20 split leaves at least 2 points in
# every split (verified by this module's own tests).
MINIMUM_SAMPLES_FOR_VALIDATION = 10

TRAIN_FRACTION = 0.6
VALIDATION_FRACTION = 0.2
# test_fraction is whatever remains after train + validation.

# Overfitting is flagged only when BOTH hold: validation MAE is at
# least OVERFITTING_MAE_RATIO times training MAE, AND the absolute gap
# between them exceeds OVERFITTING_MIN_ABSOLUTE_GAP. The ratio alone is
# not enough — when training MAE is near zero, a trivial absolute
# difference can look like a huge ratio (validation MAE "10x" training
# MAE while both are negligibly small is not overfitting). The absolute
# gap alone is not enough either — it would never fire for metrics on a
# small numeric scale. Both are coarse, documented heuristics, not a
# statistical test.
OVERFITTING_MAE_RATIO = 1.5
OVERFITTING_MIN_ABSOLUTE_GAP = 0.5

DATA_QUALITY_GOOD = "good"
DATA_QUALITY_LIMITED = "limited"
DATA_QUALITY_INSUFFICIENT = "insufficient"


class InsufficientValidationDataError(ValueError):
    def __init__(self, sample_count: int, minimum_required: int):
        self.sample_count = sample_count
        self.minimum_required = minimum_required
        super().__init__(
            "Insufficient historical data for a reliable personalized "
            f"prediction ({sample_count} sample(s) available, "
            f"{minimum_required} required for train/validation/test "
            "evaluation)."
        )


@dataclass(frozen=True)
class SplitMetrics:
    sample_count: int
    mae: float | None
    rmse: float | None
    # None whenever r2 is mathematically undefined for this split (fewer
    # than 2 points, or every true value identical — both make the R²
    # denominator zero) — never a fabricated 0.0 or 1.0 standing in for
    # "not computable."
    r2: float | None


@dataclass(frozen=True)
class ValidationResult:
    model_name: str
    model_version: str
    total_sample_count: int
    train: SplitMetrics
    validation: SplitMetrics
    test: SplitMetrics
    overfitting_detected: bool
    data_quality: str
    warnings: list[str] = field(default_factory=list)


def chronological_split(
    examples: list[TrainingExample],
) -> tuple[list[TrainingExample], list[TrainingExample], list[TrainingExample]]:
    """Earliest points train the model; later points validate it; the
    latest points are the untouched test set — the only split order
    that avoids using future information to evaluate a prediction of
    the past."""
    ordered = sorted(examples, key=lambda e: e.days_since_first)
    n = len(ordered)
    train_end = max(1, round(n * TRAIN_FRACTION))
    validation_end = train_end + max(1, round(n * VALIDATION_FRACTION))
    validation_end = min(validation_end, n)
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


def _compute_split_metrics(true_scores: list[float], predicted_scores: list[float]) -> SplitMetrics:
    n = len(true_scores)
    if n == 0:
        return SplitMetrics(sample_count=0, mae=None, rmse=None, r2=None)

    mae = float(mean_absolute_error(true_scores, predicted_scores))
    rmse = float(mean_squared_error(true_scores, predicted_scores) ** 0.5)

    r2 = None
    if n >= 2 and len(set(true_scores)) > 1:
        r2 = float(r2_score(true_scores, predicted_scores))

    return SplitMetrics(sample_count=n, mae=mae, rmse=rmse, r2=r2)


def _evaluate(model: Ridge, examples: list[TrainingExample]) -> SplitMetrics:
    if not examples:
        return SplitMetrics(sample_count=0, mae=None, rmse=None, r2=None)
    x = np.array([[e.days_since_first] for e in examples], dtype=float)
    y_true = [e.score for e in examples]
    y_pred = model.predict(x).tolist()
    return _compute_split_metrics(y_true, y_pred)


def classify_validation_data_quality(total_sample_count: int) -> str:
    if total_sample_count < MINIMUM_SAMPLES_FOR_VALIDATION:
        return DATA_QUALITY_INSUFFICIENT
    if total_sample_count < 20:
        return DATA_QUALITY_LIMITED
    return DATA_QUALITY_GOOD


def validate_model(examples: list[TrainingExample]) -> ValidationResult:
    if len(examples) < MINIMUM_SAMPLES_FOR_VALIDATION:
        raise InsufficientValidationDataError(len(examples), MINIMUM_SAMPLES_FOR_VALIDATION)

    train, validation, test = chronological_split(examples)
    warnings: list[str] = []

    x_train = np.array([[e.days_since_first] for e in train], dtype=float)
    y_train = [e.score for e in train]
    model = Ridge(alpha=1.0)
    model.fit(x_train, y_train)

    train_metrics = _evaluate(model, train)
    validation_metrics = _evaluate(model, validation)
    test_metrics = _evaluate(model, test)

    if validation_metrics.sample_count < 2:
        warnings.append(
            "Validation split has fewer than 2 samples — overfitting "
            "cannot be reliably assessed."
        )
    if test_metrics.sample_count < 2:
        warnings.append(
            "Test split has fewer than 2 samples — reported test "
            "metrics are not statistically meaningful."
        )

    overfitting_detected = False
    if (
        train_metrics.mae is not None
        and validation_metrics.mae is not None
        and validation_metrics.sample_count >= 2
        and validation_metrics.mae > train_metrics.mae * OVERFITTING_MAE_RATIO
        and (validation_metrics.mae - train_metrics.mae) > OVERFITTING_MIN_ABSOLUTE_GAP
    ):
        overfitting_detected = True
        warnings.append(
            "Validation error is substantially higher than training "
            "error — possible overfitting to the training split."
        )

    return ValidationResult(
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
        total_sample_count=len(examples),
        train=train_metrics,
        validation=validation_metrics,
        test=test_metrics,
        overfitting_detected=overfitting_detected,
        data_quality=classify_validation_data_quality(len(examples)),
        warnings=warnings,
    )
