from sqlalchemy.orm import Session

from app.db_models import MonteCarloSimulationDB
from app.monte_carlo_simulation import (
    DEFAULT_SIMULATION_COUNT,
    SimulationResult,
    compute_walk_forward_residuals,
    run_simulation,
)
from app.services.auth_service import utcnow
from app.services.id_service import next_entity_id
from app.services.ml_prediction_service import MLPredictionService
from app.services.training_examples import (
    player_specific_training_examples,
    population_training_examples,
)


class MonteCarloService:
    """Step 7: runs a real Monte Carlo simulation on top of a fresh Step
    5 point prediction, using Step 7's own walk-forward residuals as the
    uncertainty source (see app/monte_carlo_simulation.py's header for
    why this, not an invented distribution). The point prediction is
    always created and persisted first, exactly as
    POST /players/{id}/predictions would — if the simulation itself then
    fails for lack of residuals, that valid prediction is NOT rolled
    back; only the uncertainty layer is unavailable.
    """

    def __init__(self, db: Session):
        self.db = db
        self._predictions = MLPredictionService(db)

    def simulate(
        self,
        player_id: str,
        metric_name: str,
        forecast_days: int,
        model_scope: str = "population",
        num_simulations: int = DEFAULT_SIMULATION_COUNT,
        seed: int | None = None,
    ) -> MonteCarloSimulationDB:
        # Raises NoRecordedMetricError / InsufficientTrainingDataError /
        # ValueError exactly as POST /predictions would — same gates,
        # same errors, since this creates a real prediction the same way.
        prediction = self._predictions.predict(
            player_id=player_id,
            metric_name=metric_name,
            forecast_days=forecast_days,
            model_scope=model_scope,
        )

        if model_scope == "player_specific":
            examples = player_specific_training_examples(self.db, player_id, metric_name)
        else:
            examples = population_training_examples(self.db, metric_name)

        residuals = compute_walk_forward_residuals(examples)

        # Raises InsufficientUncertaintyDataError when there are too few
        # real residuals — the caller turns this into "Insufficient
        # validated measurement uncertainty data." The prediction row
        # created above stays exactly as-is (lower_bound/upper_bound
        # still None from Step 5) rather than being rolled back.
        result: SimulationResult = run_simulation(
            point_prediction=prediction.predicted_score,
            residuals=residuals,
            num_simulations=num_simulations,
            seed=seed,
        )

        simulation = MonteCarloSimulationDB(
            simulation_id=next_entity_id(self.db, "monte_carlo_simulation"),
            prediction_id=prediction.prediction_id,
            player_id=player_id,
            metric_name=metric_name,
            num_simulations=result.num_simulations,
            median=result.median,
            p05=result.p05,
            p10=result.p10,
            p25=result.p25,
            p50=result.p50,
            p75=result.p75,
            p90=result.p90,
            p95=result.p95,
            simulation_method=result.simulation_method,
            uncertainty_level=result.uncertainty_level,
            residual_sample_count=result.residual_sample_count,
            created_at=utcnow(),
        )
        self.db.add(simulation)

        # The 90% interval is the one bound this module is willing to
        # write back onto the prediction row itself — confidence_score
        # stays None permanently (see MLPredictionDB's docstring).
        prediction.lower_bound = result.p05
        prediction.upper_bound = result.p95

        self.db.commit()
        self.db.refresh(simulation)
        return simulation

    def list_for_player(
        self,
        player_id: str,
        metric_name: str | None = None,
    ) -> list[MonteCarloSimulationDB]:
        query = self.db.query(MonteCarloSimulationDB).filter(
            MonteCarloSimulationDB.player_id == player_id
        )
        if metric_name is not None:
            query = query.filter(MonteCarloSimulationDB.metric_name == metric_name)
        return query.order_by(MonteCarloSimulationDB.created_at.desc()).all()
