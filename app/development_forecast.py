import math
import random
import statistics


def _bounded_score(value: float) -> float:
    return min(max(float(value), 0.0), 100.0)


def _normalize_weakness(weakness) -> tuple[str, float]:
    if isinstance(weakness, dict):
        attribute = weakness.get("attribute")
        score = weakness.get("score")
    elif isinstance(weakness, (list, tuple)) and len(weakness) == 2:
        attribute, score = weakness
    else:
        raise ValueError(
            "each weakness must contain an attribute and score"
        )

    if not isinstance(attribute, str) or not attribute.strip():
        raise ValueError("weakness attribute cannot be empty")
    if not isinstance(score, (int, float)) or not 0 <= score <= 100:
        raise ValueError("weakness score must be between 0 and 100")

    return attribute.strip(), float(score)


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("forecast contains no simulations")

    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)

    if lower == upper:
        return sorted_values[lower]

    weight = position - lower
    return (
        sorted_values[lower] * (1 - weight)
        + sorted_values[upper] * weight
    )


def forecast_development(
    weaknesses: list,
    confidence_score: float | None,
    weeks: int,
    sessions_per_week: int,
    expected_gain_per_session: float = 0.35,
    session_volatility: float = 0.5,
    adherence_probability: float = 0.8,
    minimum_improvement: float = 5.0,
    simulations: int = 5000,
    seed: int = 42,
) -> dict:
    if weeks <= 0 or sessions_per_week <= 0:
        raise ValueError("weeks and sessions_per_week must be positive")
    if simulations < 100:
        raise ValueError("simulations must be at least 100")
    if expected_gain_per_session < 0 or session_volatility < 0:
        raise ValueError("gain and volatility cannot be negative")
    if not 0 <= adherence_probability <= 1:
        raise ValueError("adherence_probability must be between 0 and 1")
    if minimum_improvement < 0:
        raise ValueError("minimum_improvement cannot be negative")

    confidence = 0.5 if confidence_score is None else float(confidence_score)
    if not 0 <= confidence <= 1:
        raise ValueError("confidence_score must be between 0 and 1")

    total_sessions = weeks * sessions_per_week
    forecasts = []

    for weakness in weaknesses:
        attribute, starting_score = _normalize_weakness(weakness)
        randomizer = random.Random(f"{seed}:{attribute.casefold()}")
        outcomes = []

        for _ in range(simulations):
            score = starting_score

            for _ in range(total_sessions):
                if randomizer.random() > adherence_probability:
                    continue

                remaining_potential = max(0.1, (100.0 - score) / 100.0)
                mean_gain = (
                    expected_gain_per_session
                    * remaining_potential
                )
                uncertainty = session_volatility * (1.5 - confidence)
                score = _bounded_score(
                    score + randomizer.gauss(mean_gain, uncertainty)
                )

            outcomes.append(score)

        outcomes.sort()
        target = _bounded_score(starting_score + minimum_improvement)
        success_count = sum(value >= target for value in outcomes)
        median = _percentile(outcomes, 0.5)
        forecasts.append({
            "attribute": attribute,
            "starting_score": round(starting_score, 2),
            "projected_score": {
                "p10": round(_percentile(outcomes, 0.1), 2),
                "median": round(median, 2),
                "p90": round(_percentile(outcomes, 0.9), 2),
            },
            "expected_improvement": round(median - starting_score, 2),
            "target_score": round(target, 2),
            "probability_of_target": round(success_count / simulations, 4),
        })

    return {
        "method": "monte_carlo",
        "simulations": simulations,
        "seed": seed,
        "analysis_confidence": confidence,
        "assumptions": {
            "weeks": weeks,
            "sessions_per_week": sessions_per_week,
            "total_planned_sessions": total_sessions,
            "expected_gain_per_session": expected_gain_per_session,
            "session_volatility": session_volatility,
            "adherence_probability": adherence_probability,
            "minimum_improvement": minimum_improvement,
        },
        "forecasts": forecasts,
        "disclaimer": (
            "Scenario estimate, not a guarantee. Calibrate assumptions with "
            "observed player outcomes and qualified coach review."
        ),
    }


def calibrate_forecast_assumptions(
    observations: list[dict],
    bootstrap_samples: int = 1000,
    seed: int = 42,
) -> dict:
    """Estimate scenario assumptions from completed, coach-reviewed interventions."""
    if len(observations) < 3:
        raise ValueError("At least three intervention observations are required")
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")

    normalized = []
    for index, item in enumerate(observations):
        planned = item.get("planned_sessions")
        completed = item.get("completed_sessions")
        starting = item.get("starting_score")
        ending = item.get("ending_score")
        numeric = (planned, completed, starting, ending)
        if any(not isinstance(value, (int, float)) for value in numeric):
            raise ValueError(f"Observation {index}: all values must be numeric")
        if planned <= 0 or completed < 0 or completed > planned:
            raise ValueError(
                f"Observation {index}: sessions must satisfy 0 <= completed <= planned"
            )
        if not 0 <= starting <= 100 or not 0 <= ending <= 100:
            raise ValueError(f"Observation {index}: scores must be between 0 and 100")
        normalized.append({
            "planned": float(planned),
            "completed": float(completed),
            "starting": float(starting),
            "ending": float(ending),
        })

    total_planned = sum(item["planned"] for item in normalized)
    total_completed = sum(item["completed"] for item in normalized)
    completed_outcomes = [item for item in normalized if item["completed"] > 0]
    if not completed_outcomes:
        raise ValueError("At least one completed training session is required")

    gains = [
        (item["ending"] - item["starting"]) / item["completed"]
        for item in completed_outcomes
    ]

    def estimate(sample: list[dict]) -> tuple[float, float, float]:
        planned = sum(item["planned"] for item in sample)
        completed = sum(item["completed"] for item in sample)
        sample_gains = [
            (item["ending"] - item["starting"]) / item["completed"]
            for item in sample
            if item["completed"] > 0
        ]
        adherence = completed / planned
        mean_gain = max(0.0, statistics.fmean(sample_gains)) if sample_gains else 0.0
        volatility = statistics.stdev(sample_gains) if len(sample_gains) > 1 else 0.0
        return adherence, mean_gain, volatility

    adherence, expected_gain, volatility = estimate(normalized)
    randomizer = random.Random(seed)
    bootstrap = [
        estimate([randomizer.choice(normalized) for _ in normalized])
        for _ in range(bootstrap_samples)
    ]

    def interval(position: int) -> dict:
        values = sorted(item[position] for item in bootstrap)
        return {
            "p05": round(_percentile(values, 0.05), 4),
            "p95": round(_percentile(values, 0.95), 4),
        }

    return {
        "method": "coach_reviewed_bootstrap_calibration",
        "observation_count": len(normalized),
        "total_planned_sessions": round(total_planned),
        "total_completed_sessions": round(total_completed),
        "estimates": {
            "adherence_probability": round(adherence, 4),
            "expected_gain_per_session": round(expected_gain, 4),
            "session_volatility": round(volatility, 4),
        },
        "bootstrap_90_percent_intervals": {
            "adherence_probability": interval(0),
            "expected_gain_per_session": interval(1),
            "session_volatility": interval(2),
        },
        "bootstrap_samples": bootstrap_samples,
        "seed": seed,
        "warning": (
            "Observational calibration is not causal evidence. Stratify by age, "
            "baseline ability, intervention, and coach when sufficient data exist."
        ),
    }


def analyze_forecast_sensitivity(
    weaknesses: list,
    confidence_score: float | None,
    weeks: int,
    sessions_per_week: int,
    expected_gain_per_session: float = 0.35,
    session_volatility: float = 0.5,
    adherence_probability: float = 0.8,
    simulations: int = 1000,
    seed: int = 42,
) -> dict:
    """Expose how forecast conclusions change across plausible assumptions."""
    adherence_values = sorted({
        round(max(0.0, adherence_probability - 0.15), 4),
        round(adherence_probability, 4),
        round(min(1.0, adherence_probability + 0.15), 4),
    })
    gain_values = sorted({
        round(expected_gain_per_session * 0.75, 4),
        round(expected_gain_per_session, 4),
        round(expected_gain_per_session * 1.25, 4),
    })
    scenarios = []
    by_attribute = {}
    for adherence in adherence_values:
        for gain in gain_values:
            result = forecast_development(
                weaknesses=weaknesses,
                confidence_score=confidence_score,
                weeks=weeks,
                sessions_per_week=sessions_per_week,
                expected_gain_per_session=gain,
                session_volatility=session_volatility,
                adherence_probability=adherence,
                simulations=simulations,
                seed=seed,
            )
            scenario = {
                "adherence_probability": adherence,
                "expected_gain_per_session": gain,
                "forecasts": result["forecasts"],
            }
            scenarios.append(scenario)
            for forecast in result["forecasts"]:
                values = by_attribute.setdefault(
                    forecast["attribute"],
                    {"median": [], "probability": []},
                )
                values["median"].append(forecast["projected_score"]["median"])
                values["probability"].append(forecast["probability_of_target"])

    sensitivity = {
        attribute: {
            "projected_median_range": [min(values["median"]), max(values["median"])],
            "probability_of_target_range": [
                min(values["probability"]),
                max(values["probability"]),
            ],
        }
        for attribute, values in sorted(by_attribute.items())
    }
    return {
        "method": "monte_carlo_grid_sensitivity",
        "scenario_count": len(scenarios),
        "simulations_per_scenario": simulations,
        "varied_assumptions": {
            "adherence_probability": adherence_values,
            "expected_gain_per_session": gain_values,
        },
        "sensitivity": sensitivity,
        "scenarios": scenarios,
        "interpretation": (
            "Wide ranges mean the conclusion depends strongly on assumptions and "
            "should not be presented as a stable player forecast."
        ),
    }
