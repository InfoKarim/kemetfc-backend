"""Calibration and selective-prediction metrics for model confidence scores."""

from __future__ import annotations

import math
from collections import defaultdict


def _validated_records(records: list[dict]) -> list[tuple[float, int]]:
    values = []
    for index, record in enumerate(records):
        confidence = record.get("confidence")
        correct = record.get("correct")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError(f"Record {index}: confidence must be between 0 and 1")
        if correct not in (True, False, 0, 1):
            raise ValueError(f"Record {index}: correct must be boolean")
        values.append((float(confidence), int(bool(correct))))
    if not values:
        raise ValueError("At least one confidence record is required")
    return values


def evaluate_calibration(records: list[dict], bin_count: int = 10) -> dict:
    """Return reliability bins, ECE, maximum gap, Brier score, and log loss."""
    if bin_count < 2:
        raise ValueError("bin_count must be at least 2")
    values = _validated_records(records)
    bins = [[] for _ in range(bin_count)]
    for confidence, correct in values:
        index = min(int(confidence * bin_count), bin_count - 1)
        bins[index].append((confidence, correct))

    reliability = []
    weighted_gap = 0.0
    maximum_gap = 0.0
    for index, bucket in enumerate(bins):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        if bucket:
            mean_confidence = sum(value[0] for value in bucket) / len(bucket)
            accuracy = sum(value[1] for value in bucket) / len(bucket)
            gap = abs(mean_confidence - accuracy)
            weighted_gap += gap * len(bucket) / len(values)
            maximum_gap = max(maximum_gap, gap)
        else:
            mean_confidence = accuracy = gap = None
        reliability.append({
            "lower": round(lower, 4),
            "upper": round(upper, 4),
            "count": len(bucket),
            "mean_confidence": round(mean_confidence, 4)
            if mean_confidence is not None else None,
            "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "calibration_gap": round(gap, 4) if gap is not None else None,
        })

    brier = sum((confidence - correct) ** 2 for confidence, correct in values) / len(values)
    epsilon = 1e-12
    negative_log_likelihood = -sum(
        correct * math.log(min(max(confidence, epsilon), 1 - epsilon))
        + (1 - correct) * math.log(min(max(1 - confidence, epsilon), 1 - epsilon))
        for confidence, correct in values
    ) / len(values)
    return {
        "sample_count": len(values),
        "bin_count": bin_count,
        "expected_calibration_error": round(weighted_gap, 4),
        "maximum_calibration_error": round(maximum_gap, 4),
        "brier_score": round(brier, 4),
        "negative_log_likelihood": round(negative_log_likelihood, 4),
        "reliability_bins": reliability,
        "interpretation": (
            "Lower ECE, Brier score, and log loss are better. Calibration must be "
            "measured on held-out data from the intended camera population."
        ),
    }


def selective_risk_curve(
    records: list[dict],
    thresholds: list[float] | None = None,
) -> list[dict]:
    """Measure error after abstaining below each confidence threshold."""
    values = _validated_records(records)
    configured = thresholds or [value / 10 for value in range(11)]
    if any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in configured):
        raise ValueError("thresholds must be between 0 and 1")

    curve = []
    for threshold in sorted(set(float(value) for value in configured)):
        accepted = [correct for confidence, correct in values if confidence >= threshold]
        coverage = len(accepted) / len(values)
        accuracy = sum(accepted) / len(accepted) if accepted else None
        curve.append({
            "threshold": round(threshold, 4),
            "accepted": len(accepted),
            "abstained": len(values) - len(accepted),
            "coverage": round(coverage, 4),
            "accuracy": round(accuracy, 4) if accuracy is not None else None,
            "selective_risk": round(1 - accuracy, 4) if accuracy is not None else None,
        })
    return curve


def choose_abstention_policy(
    records: list[dict],
    maximum_error_rate: float = 0.1,
    minimum_coverage: float = 0.5,
) -> dict:
    """Choose the highest-coverage observed threshold satisfying a risk budget."""
    if not 0 <= maximum_error_rate <= 1:
        raise ValueError("maximum_error_rate must be between 0 and 1")
    if not 0 <= minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be between 0 and 1")
    values = _validated_records(records)
    thresholds = sorted({0.0, 1.0, *(confidence for confidence, _ in values)})
    curve = selective_risk_curve(records, thresholds)
    candidates = [
        point for point in curve
        if point["coverage"] >= minimum_coverage
        and point["selective_risk"] is not None
        and point["selective_risk"] <= maximum_error_rate
    ]
    selected = max(candidates, key=lambda point: (point["coverage"], -point["threshold"]), default=None)
    return {
        "approved": selected is not None,
        "maximum_error_rate": maximum_error_rate,
        "minimum_coverage": minimum_coverage,
        "selected": selected,
        "curve": curve,
        "failure_reason": None if selected else (
            "No confidence threshold meets both the error and coverage requirements."
        ),
    }


def evaluate_calibration_slices(
    records: list[dict],
    slice_key: str,
    bin_count: int = 10,
    minimum_slice_size: int = 20,
) -> dict:
    """Expose calibration failures hidden by an aggregate score."""
    if minimum_slice_size <= 0:
        raise ValueError("minimum_slice_size must be positive")
    grouped = defaultdict(list)
    for index, record in enumerate(records):
        value = record.get(slice_key)
        if value is None or str(value).strip() == "":
            raise ValueError(f"Record {index}: {slice_key} is required")
        grouped[str(value)].append(record)
    if not grouped:
        raise ValueError("At least one confidence record is required")

    slices = {}
    warnings = []
    for name, items in sorted(grouped.items()):
        slices[name] = evaluate_calibration(items, bin_count)
        if len(items) < minimum_slice_size:
            warnings.append(
                f"Slice {name!r} has {len(items)} samples; minimum is {minimum_slice_size}."
            )
    return {
        "slice_key": slice_key,
        "minimum_slice_size": minimum_slice_size,
        "slices": slices,
        "warnings": warnings,
    }
