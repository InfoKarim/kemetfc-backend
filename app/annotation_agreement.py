"""Transparent agreement metrics for independently labelled football data."""

from __future__ import annotations

from collections import defaultdict

from app.ml_evaluation import intersection_over_union


def _validate_threshold(value: float, name: str) -> None:
    if not 0 < value <= 1:
        raise ValueError(f"{name} must be between 0 and 1")


def evaluate_detection_agreement(
    first_annotations: list[dict],
    second_annotations: list[dict],
    iou_threshold: float = 0.5,
    minimum_f1: float = 0.8,
) -> dict:
    """Measure symmetric object-annotation agreement without naming a gold annotator."""
    _validate_threshold(iou_threshold, "iou_threshold")
    _validate_threshold(minimum_f1, "minimum_f1")

    first_by_image = defaultdict(list)
    second_by_image = defaultdict(list)
    for item in first_annotations:
        first_by_image[item["image_id"]].append(item)
    for item in second_annotations:
        second_by_image[item["image_id"]].append(item)

    class_counts = defaultdict(lambda: {"first": 0, "second": 0, "matched": 0})
    matched_ious: list[float] = []

    for image_id in sorted(set(first_by_image) | set(second_by_image)):
        first = first_by_image[image_id]
        second = second_by_image[image_id]
        used_second: set[int] = set()

        for item in first:
            label = item["class_name"]
            class_counts[label]["first"] += 1
            candidates = [
                (index, intersection_over_union(item["bbox"], candidate["bbox"]))
                for index, candidate in enumerate(second)
                if index not in used_second and candidate["class_name"] == label
            ]
            best = max(candidates, key=lambda value: value[1], default=None)
            if best is not None and best[1] >= iou_threshold:
                used_second.add(best[0])
                class_counts[label]["matched"] += 1
                matched_ious.append(best[1])

        for item in second:
            class_counts[item["class_name"]]["second"] += 1

    per_class = {}
    total_first = total_second = total_matched = 0
    for label, counts in sorted(class_counts.items()):
        denominator = counts["first"] + counts["second"]
        f1 = 2 * counts["matched"] / denominator if denominator else 1.0
        per_class[label] = {
            "annotator_a_count": counts["first"],
            "annotator_b_count": counts["second"],
            "matched_count": counts["matched"],
            "agreement_f1": round(f1, 4),
        }
        total_first += counts["first"]
        total_second += counts["second"]
        total_matched += counts["matched"]

    denominator = total_first + total_second
    overall_f1 = 2 * total_matched / denominator if denominator else 1.0
    return {
        "method": "symmetric_iou_matching",
        "iou_threshold": iou_threshold,
        "minimum_f1": minimum_f1,
        "annotator_a_count": total_first,
        "annotator_b_count": total_second,
        "matched_count": total_matched,
        "agreement_f1": round(overall_f1, 4),
        "mean_matched_iou": round(sum(matched_ious) / len(matched_ious), 4)
        if matched_ious else None,
        "per_class": per_class,
        "passed": overall_f1 >= minimum_f1,
        "note": (
            "This measures reproducibility between annotators; it does not prove "
            "that either annotation set is correct. Resolve disagreements by review."
        ),
    }


def evaluate_event_agreement(
    first_annotations: list[dict],
    second_annotations: list[dict],
    tolerance_seconds: float = 1.0,
    minimum_f1: float = 0.8,
) -> dict:
    """Measure agreement for event type and timestamp annotations."""
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")
    _validate_threshold(minimum_f1, "minimum_f1")

    matched_second: set[int] = set()
    timing_errors: list[float] = []
    per_type = defaultdict(lambda: {"first": 0, "second": 0, "matched": 0})
    for event in first_annotations:
        event_type = event["event_type"]
        per_type[event_type]["first"] += 1
        candidates = [
            (
                index,
                abs(
                    float(event["timestamp_seconds"])
                    - float(candidate["timestamp_seconds"])
                ),
            )
            for index, candidate in enumerate(second_annotations)
            if index not in matched_second and candidate["event_type"] == event_type
        ]
        best = min(candidates, key=lambda value: value[1], default=None)
        if best is not None and best[1] <= tolerance_seconds:
            matched_second.add(best[0])
            per_type[event_type]["matched"] += 1
            timing_errors.append(best[1])

    for event in second_annotations:
        per_type[event["event_type"]]["second"] += 1

    first_count = len(first_annotations)
    second_count = len(second_annotations)
    matched_count = len(matched_second)
    denominator = first_count + second_count
    agreement_f1 = 2 * matched_count / denominator if denominator else 1.0
    return {
        "method": "symmetric_timestamp_matching",
        "tolerance_seconds": tolerance_seconds,
        "minimum_f1": minimum_f1,
        "annotator_a_count": first_count,
        "annotator_b_count": second_count,
        "matched_count": matched_count,
        "agreement_f1": round(agreement_f1, 4),
        "mean_absolute_timing_error_seconds": round(
            sum(timing_errors) / len(timing_errors), 4
        ) if timing_errors else None,
        "per_event_type": {
            label: {
                "annotator_a_count": values["first"],
                "annotator_b_count": values["second"],
                "matched_count": values["matched"],
                "agreement_f1": round(
                    2 * values["matched"] / (values["first"] + values["second"]),
                    4,
                ) if values["first"] + values["second"] else 1.0,
            }
            for label, values in sorted(per_type.items())
        },
        "passed": agreement_f1 >= minimum_f1,
    }
