"""Research-grade comparisons and sliced evaluation for football ML experiments."""

from __future__ import annotations

import math
import random
from collections import defaultdict


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def wilson_interval(successes: int, total: int, z_score: float = 1.96) -> list[float]:
    """Return a Wilson score interval for a binomial success rate."""
    if total <= 0:
        raise ValueError("total must be positive")
    if successes < 0 or successes > total:
        raise ValueError("successes must be between zero and total")
    if z_score <= 0:
        raise ValueError("z_score must be positive")
    proportion = successes / total
    denominator = 1 + z_score**2 / total
    centre = (proportion + z_score**2 / (2 * total)) / denominator
    margin = (
        z_score
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z_score**2 / (4 * total**2)
        )
        / denominator
    )
    return [round(max(0.0, centre - margin), 4), round(min(1.0, centre + margin), 4)]


def evaluate_performance_slices(
    records: list[dict],
    slice_keys: list[str],
    minimum_samples: int = 30,
) -> dict:
    """Report accuracy and uncertainty for deployment-relevant cohorts."""
    if not records:
        raise ValueError("At least one evaluation record is required")
    if not slice_keys:
        raise ValueError("At least one slice key is required")
    if minimum_samples <= 0:
        raise ValueError("minimum_samples must be positive")

    groups = defaultdict(list)
    for index, record in enumerate(records):
        if record.get("correct") not in (True, False, 0, 1):
            raise ValueError(f"Record {index}: correct must be boolean")
        for key in slice_keys:
            value = record.get(key)
            if value is None or str(value).strip() == "":
                raise ValueError(f"Record {index}: {key} is required")
            groups[(key, str(value))].append(int(bool(record["correct"])))

    slices = {}
    warnings = []
    for (key, value), outcomes in sorted(groups.items()):
        successes = sum(outcomes)
        total = len(outcomes)
        identifier = f"{key}={value}"
        slices[identifier] = {
            "slice_key": key,
            "slice_value": value,
            "sample_count": total,
            "correct_count": successes,
            "accuracy": round(successes / total, 4),
            "accuracy_95_percent_interval": wilson_interval(successes, total),
            "sufficient_evidence": total >= minimum_samples,
        }
        if total < minimum_samples:
            warnings.append(
                f"Slice {identifier!r} has {total} samples; minimum is {minimum_samples}."
            )

    accuracies = [item["accuracy"] for item in slices.values()]
    return {
        "minimum_samples": minimum_samples,
        "slice_keys": slice_keys,
        "slices": slices,
        "maximum_accuracy_gap": round(max(accuracies) - min(accuracies), 4),
        "warnings": warnings,
    }


def paired_bootstrap_comparison(
    baseline_records: list[dict],
    candidate_records: list[dict],
    bootstrap_samples: int = 5000,
    seed: int = 42,
) -> dict:
    """Compare models on identical examples with a paired bootstrap interval."""
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")

    def index_records(records: list[dict], label: str) -> dict[str, int]:
        indexed = {}
        for index, record in enumerate(records):
            example_id = str(record.get("example_id", "")).strip()
            if not example_id:
                raise ValueError(f"{label} record {index}: example_id is required")
            if example_id in indexed:
                raise ValueError(f"{label}: duplicate example_id {example_id!r}")
            if record.get("correct") not in (True, False, 0, 1):
                raise ValueError(f"{label} record {index}: correct must be boolean")
            indexed[example_id] = int(bool(record["correct"]))
        return indexed

    baseline = index_records(baseline_records, "baseline")
    candidate = index_records(candidate_records, "candidate")
    if not baseline or set(baseline) != set(candidate):
        raise ValueError("baseline and candidate must contain the same non-empty examples")

    example_ids = sorted(baseline)
    differences = [candidate[key] - baseline[key] for key in example_ids]
    observed = sum(differences) / len(differences)
    randomizer = random.Random(seed)
    bootstrap = []
    for _ in range(bootstrap_samples):
        sample = [randomizer.choice(differences) for _ in differences]
        bootstrap.append(sum(sample) / len(sample))
    bootstrap.sort()
    lower = _percentile(bootstrap, 0.025)
    upper = _percentile(bootstrap, 0.975)
    probability_improvement = sum(value > 0 for value in bootstrap) / bootstrap_samples
    return {
        "method": "paired_nonparametric_bootstrap",
        "example_count": len(example_ids),
        "baseline_accuracy": round(sum(baseline.values()) / len(baseline), 4),
        "candidate_accuracy": round(sum(candidate.values()) / len(candidate), 4),
        "absolute_accuracy_difference": round(observed, 4),
        "difference_95_percent_interval": [round(lower, 4), round(upper, 4)],
        "bootstrap_probability_of_improvement": round(probability_improvement, 4),
        "statistically_clear_improvement": lower > 0,
        "bootstrap_samples": bootstrap_samples,
        "seed": seed,
    }


def evaluate_ablation_study(
    runs: list[dict],
    full_run_name: str,
    metric_name: str,
    higher_is_better: bool = True,
) -> dict:
    """Quantify the change produced by removing each system component."""
    if len(runs) < 2:
        raise ValueError("Ablation requires a full run and at least one variant")
    indexed = {}
    for index, run in enumerate(runs):
        name = str(run.get("name", "")).strip()
        if not name or name in indexed:
            raise ValueError(f"Run {index}: names must be non-empty and unique")
        metric = run.get(metric_name)
        if not isinstance(metric, (int, float)) or not math.isfinite(metric):
            raise ValueError(f"Run {name!r}: {metric_name} must be finite and numeric")
        indexed[name] = float(metric)
    if full_run_name not in indexed:
        raise ValueError("full_run_name was not found")

    full_value = indexed[full_run_name]
    variants = []
    for name, value in indexed.items():
        if name == full_run_name:
            continue
        contribution = full_value - value if higher_is_better else value - full_value
        variants.append({
            "name": name,
            "metric_value": round(value, 6),
            "estimated_component_contribution": round(contribution, 6),
            "degraded_without_component": contribution > 0,
        })
    variants.sort(key=lambda item: item["estimated_component_contribution"], reverse=True)
    return {
        "metric_name": metric_name,
        "higher_is_better": higher_is_better,
        "full_run": {"name": full_run_name, "metric_value": round(full_value, 6)},
        "variants": variants,
        "note": (
            "Ablations identify component dependence, not causal effects outside "
            "the fixed dataset and experimental protocol."
        ),
    }
