"""Monte Carlo simulation layer for player metric forecasts (Step 7 of
the Player Development / ML / Monte Carlo module).

HONESTY BOUNDARY — this is the same discipline this codebase's other
Monte Carlo work (development_forecast.py, and the iOS action-
recognition module) already applies:

- Never retrains the model, never invents a label, never replaces the
  Step 5 point prediction (predict_future_score) — this module only
  characterizes how uncertain that point prediction is.
- The uncertainty source is REAL: residuals (actual - predicted) from
  walk-forward validation — for every point after the first
  WALK_FORWARD_MINIMUM_TRAINING_SIZE points, train Ridge on every
  strictly EARLIER point only, predict this one point, record the real
  error. Every residual is genuinely out-of-sample and chronologically
  honest (never uses a later point to help "predict" an earlier one) —
  this is standard walk-forward/expanding-window cross-validation, not
  an invented distribution. It is also more data-efficient than a
  single fixed train/validation/test split (app/ml_validation.py) for
  the tiny sample sizes this codebase actually has today, which is why
  this module computes its own residuals rather than reusing that
  split's leftover test residuals directly.
- Refuses to simulate below MINIMUM_RESIDUALS_FOR_SIMULATION, raising
  InsufficientUncertaintyDataError — the caller turns this into
  "Insufficient validated measurement uncertainty data. Simulation
  confidence is not available," never a fabricated interval.
- Percentiles come from resampling the REAL residuals with replacement
  (bootstrap) onto the point prediction — every simulated value is
  "point prediction + a real historical error," never a value drawn
  from an assumed/invented probability distribution (no Gaussian, no
  hand-picked variance).
- uncertainty_level is derived ONLY from how much real evidence backs
  the interval (residual_sample_count) — never from R² or any other
  accuracy-flavored number, and never presented as prediction accuracy.
"""

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import Ridge

from app.ml_prediction import TrainingExample


DEFAULT_SIMULATION_COUNT = 10_000

# Matches ml_prediction.py's own MINIMUM_TRAINING_SAMPLES — the walk-
# forward loop trains on at least this many earlier points before it
# starts recording residuals, so every residual comes from a model at
# least as well-fit as the one predict_future_score itself would
# refuse to run below.
WALK_FORWARD_MINIMUM_TRAINING_SIZE = 5

# A pragmatic, documented (not empirically validated) floor: fewer than
# 3 real observed errors is not enough to bootstrap any interval from,
# real or otherwise.
MINIMUM_RESIDUALS_FOR_SIMULATION = 3

UNCERTAINTY_INSUFFICIENT_DATA = "insufficient_data"
UNCERTAINTY_LOW = "low"
UNCERTAINTY_MODERATE = "moderate"
UNCERTAINTY_HIGH = "high"


class InsufficientUncertaintyDataError(ValueError):
    def __init__(self, residual_count: int, minimum_required: int):
        self.residual_count = residual_count
        self.minimum_required = minimum_required
        super().__init__(
            "Insufficient validated measurement uncertainty data. "
            "Simulation confidence is not available "
            f"({residual_count} real historical error sample(s) available, "
            f"{minimum_required} required)."
        )


@dataclass(frozen=True)
class SimulationResult:
    num_simulations: int
    median: float
    p05: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    p95: float
    simulation_method: str
    uncertainty_level: str
    residual_sample_count: int


def compute_walk_forward_residuals(
    examples: list[TrainingExample],
    minimum_training_size: int = WALK_FORWARD_MINIMUM_TRAINING_SIZE,
) -> list[float]:
    """Real, out-of-sample prediction errors — see module docstring."""
    ordered = sorted(examples, key=lambda e: e.days_since_first)
    residuals: list[float] = []

    for i in range(minimum_training_size, len(ordered)):
        train = ordered[:i]
        target = ordered[i]

        x_train = np.array([[e.days_since_first] for e in train], dtype=float)
        y_train = [e.score for e in train]
        model = Ridge(alpha=1.0)
        model.fit(x_train, y_train)

        predicted = float(model.predict([[target.days_since_first]])[0])
        predicted = max(0.0, predicted)  # same clip as ml_prediction.py

        residuals.append(target.score - predicted)

    return residuals


def _classify_uncertainty_level(residual_count: int) -> str:
    # Reflects how much real evidence backs the interval — NOT how
    # narrow the interval is (no validated per-metric-scale width
    # benchmark exists to judge that against) and NOT model accuracy.
    if residual_count < MINIMUM_RESIDUALS_FOR_SIMULATION:
        return UNCERTAINTY_INSUFFICIENT_DATA
    if residual_count < 8:
        return UNCERTAINTY_LOW
    if residual_count < 15:
        return UNCERTAINTY_MODERATE
    return UNCERTAINTY_HIGH


def run_simulation(
    point_prediction: float,
    residuals: list[float],
    num_simulations: int = DEFAULT_SIMULATION_COUNT,
    seed: int | None = None,
) -> SimulationResult:
    if num_simulations < 1:
        raise ValueError("num_simulations must be positive")
    if len(residuals) < MINIMUM_RESIDUALS_FOR_SIMULATION:
        raise InsufficientUncertaintyDataError(len(residuals), MINIMUM_RESIDUALS_FOR_SIMULATION)

    rng = np.random.default_rng(seed)
    sampled_residuals = rng.choice(np.array(residuals, dtype=float), size=num_simulations, replace=True)
    # Same non-negative clip as the point prediction itself (ml_prediction.py) —
    # no real score in this system is negative.
    simulated_scores = np.maximum(0.0, point_prediction + sampled_residuals)

    p05, p10, p25, p50, p75, p90, p95 = (
        float(value) for value in np.percentile(simulated_scores, [5, 10, 25, 50, 75, 90, 95])
    )

    return SimulationResult(
        num_simulations=num_simulations,
        median=p50,
        p05=p05, p10=p10, p25=p25, p50=p50, p75=p75, p90=p90, p95=p95,
        simulation_method="bootstrap_walk_forward_residual_resampling",
        uncertainty_level=_classify_uncertainty_level(len(residuals)),
        residual_sample_count=len(residuals),
    )
