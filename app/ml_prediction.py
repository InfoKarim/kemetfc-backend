"""Baseline ML model for player metric prediction (Step 5 of the Player
Development / ML / Monte Carlo module).

HONESTY BOUNDARY, matching every prior step this session:
- Predicts from the ONLY features this codebase actually has real data
  for: a metric's own historical (elapsed days, score) pairs. The much
  longer feature list in the product spec (training attendance,
  drill completion, match minutes, training intensity, ...) has no
  backing data anywhere in this schema yet (confirmed in the Step 1
  audit) — using it would mean inventing input data, which this module
  never does. Adding those features is future work gated on that data
  actually existing.
- Refuses to fit or predict below MINIMUM_TRAINING_SAMPLES, raising
  InsufficientTrainingDataError instead of a number — this is what
  the caller turns into "Insufficient historical data for a reliable
  personalized prediction."
- Produces a POINT prediction only. No confidence interval, standard
  error, or bound is computed here — Step 7 (Monte Carlo) is the
  uncertainty layer; bolting an ad hoc interval onto a 5-10-sample
  linear fit here would be exactly the kind of unvalidated number this
  codebase's Monte Carlo work already refused to invent.
- Ridge regression (this module's one candidate) with scikit-learn's
  own default alpha=1.0 — an unremarked-on, standard baseline choice,
  not tuned. Hyperparameter selection belongs to Step 6 (validation).
"""

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import Ridge


# A pragmatic floor for fitting even a 1-feature linear model without
# the fit being dominated by 2-3 arbitrary points — NOT an empirically
# validated minimum sample size (no such study exists in this codebase;
# see the Monte Carlo / trend-calculation modules for the same caveat
# about unvalidated thresholds). Revisit once Step 6 validation has
# real held-out accuracy numbers to justify a different floor.
MINIMUM_TRAINING_SAMPLES = 5

MODEL_NAME = "ridge_regression"
MODEL_VERSION = "baseline_v1"


@dataclass(frozen=True)
class TrainingExample:
    days_since_first: int
    score: float


@dataclass(frozen=True)
class PredictionResult:
    predicted_score: float
    model_name: str
    model_version: str
    training_sample_count: int


class InsufficientTrainingDataError(ValueError):
    def __init__(self, sample_count: int, minimum_required: int):
        self.sample_count = sample_count
        self.minimum_required = minimum_required
        super().__init__(
            "Insufficient historical data for a reliable personalized "
            f"prediction ({sample_count} sample(s) available, "
            f"{minimum_required} required)."
        )


def predict_future_score(
    examples: list[TrainingExample],
    target_days_since_first: int,
) -> PredictionResult:
    """Fits Ridge regression on ``examples`` (score as a function of
    elapsed days) and evaluates it at ``target_days_since_first`` — a
    caller-supplied x-coordinate, not derived from the training set
    itself, so a population-pooled fit can still be evaluated at one
    specific player's own elapsed-time horizon (see
    MLPredictionService._population_training_examples)."""
    if len(examples) < MINIMUM_TRAINING_SAMPLES:
        raise InsufficientTrainingDataError(len(examples), MINIMUM_TRAINING_SAMPLES)

    x = np.array([[example.days_since_first] for example in examples], dtype=float)
    y = np.array([example.score for example in examples], dtype=float)

    model = Ridge(alpha=1.0)
    model.fit(x, y)

    predicted = float(model.predict([[target_days_since_first]])[0])
    # Scores in this system are never negative (ratings, distances,
    # counts) — this is the one clip this module is willing to defend
    # without a per-metric bounds registry. It does not stop a tiny
    # sample from extrapolating to an implausibly high number; that is
    # a known, documented limitation of a baseline linear fit on n<10,
    # not something this module papers over silently (see model report).
    predicted = max(0.0, predicted)

    return PredictionResult(
        predicted_score=predicted,
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
        training_sample_count=len(examples),
    )
