"""Dependency-free evaluation and release gates for football detections."""

from __future__ import annotations

from collections import defaultdict


DEFAULT_THRESHOLDS = {
    "player_recall": 0.80,
    "ball_recall": 0.65,
    "macro_f1": 0.70,
    "event_precision": 0.70,
    "event_recall": 0.60,
    "tracking_recall": 0.70,
    "maximum_id_switch_rate": 0.10,
}


def intersection_over_union(first: list[float], second: list[float]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union else 0.0


def _score(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def evaluate_detections(
    ground_truth: list[dict],
    predictions: list[dict],
    iou_threshold: float = 0.5,
) -> dict:
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be between 0 and 1")
    truth_by_image = defaultdict(list)
    predictions_by_image = defaultdict(list)
    for item in ground_truth:
        truth_by_image[item["image_id"]].append(item)
    for item in predictions:
        predictions_by_image[item["image_id"]].append(item)

    counts = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    errors = []
    image_ids = sorted(set(truth_by_image) | set(predictions_by_image))
    for image_id in image_ids:
        truths = truth_by_image[image_id]
        matched = set()
        ordered_predictions = sorted(
            predictions_by_image[image_id], key=lambda item: item.get("confidence", 0), reverse=True
        )
        for prediction in ordered_predictions:
            label = prediction["class_name"]
            candidates = [
                (index, intersection_over_union(prediction["bbox"], truth["bbox"]))
                for index, truth in enumerate(truths)
                if index not in matched and truth["class_name"] == label
            ]
            best = max(candidates, key=lambda item: item[1], default=None)
            if best and best[1] >= iou_threshold:
                matched.add(best[0])
                counts[label]["tp"] += 1
            else:
                counts[label]["fp"] += 1
                errors.append({"image_id": image_id, "type": "false_positive", "class_name": label})
        for index, truth in enumerate(truths):
            if index not in matched:
                label = truth["class_name"]
                counts[label]["fn"] += 1
                errors.append({"image_id": image_id, "type": "false_negative", "class_name": label})

    per_class = {label: _score(**values) for label, values in sorted(counts.items())}
    totals = {name: sum(values[name] for values in counts.values()) for name in ("tp", "fp", "fn")}
    macro_f1 = sum(value["f1"] for value in per_class.values()) / len(per_class) if per_class else 0
    return {
        "iou_threshold": iou_threshold,
        "overall": {**_score(**totals), "macro_f1": round(macro_f1, 4)},
        "per_class": per_class,
        "error_count": len(errors),
        "errors": errors,
    }


def evaluate_events(
    ground_truth: list[dict],
    predictions: list[dict],
    tolerance_seconds: float = 1.0,
) -> dict:
    if tolerance_seconds < 0:
        raise ValueError("tolerance_seconds cannot be negative")
    matched = set()
    true_positives = 0
    errors = []
    for prediction in predictions:
        candidates = [
            (index, abs(float(prediction["timestamp_seconds"]) - float(truth["timestamp_seconds"])))
            for index, truth in enumerate(ground_truth)
            if index not in matched and truth["event_type"] == prediction["event_type"]
        ]
        best = min(candidates, key=lambda item: item[1], default=None)
        if best and best[1] <= tolerance_seconds:
            matched.add(best[0])
            true_positives += 1
        else:
            errors.append({"type": "false_positive", **prediction})
    for index, truth in enumerate(ground_truth):
        if index not in matched:
            errors.append({"type": "false_negative", **truth})
    result = _score(true_positives, len(predictions) - true_positives,
                    len(ground_truth) - true_positives)
    return {"tolerance_seconds": tolerance_seconds, **result, "errors": errors}


def evaluate_tracking(
    ground_truth: list[dict],
    predictions: list[dict],
    iou_threshold: float = 0.5,
) -> dict:
    """Report transparent association metrics without claiming HOTA/IDF1."""
    truth_by_frame = defaultdict(list)
    prediction_by_frame = defaultdict(list)
    for item in ground_truth:
        truth_by_frame[item["frame_id"]].append(item)
    for item in predictions:
        prediction_by_frame[item["frame_id"]].append(item)
    previous_assignment = {}
    matched_detections = 0
    total_truth = len(ground_truth)
    associations = 0
    id_switches = 0

    for frame_id in sorted(set(truth_by_frame) | set(prediction_by_frame)):
        available = set(range(len(prediction_by_frame[frame_id])))
        for truth in truth_by_frame[frame_id]:
            candidates = [
                (index, intersection_over_union(truth["bbox"], prediction["bbox"]))
                for index, prediction in enumerate(prediction_by_frame[frame_id])
                if index in available and prediction["class_name"] == truth["class_name"]
            ]
            best = max(candidates, key=lambda item: item[1], default=None)
            if not best or best[1] < iou_threshold:
                continue
            available.remove(best[0])
            matched_detections += 1
            prediction_id = prediction_by_frame[frame_id][best[0]].get("track_id")
            truth_id = truth.get("track_id")
            if truth_id is None or prediction_id is None:
                continue
            if truth_id in previous_assignment:
                associations += 1
                if previous_assignment[truth_id] != prediction_id:
                    id_switches += 1
            previous_assignment[truth_id] = prediction_id

    recall = matched_detections / total_truth if total_truth else 0.0
    switch_rate = id_switches / associations if associations else 0.0
    return {
        "matched_detections": matched_detections,
        "ground_truth_detections": total_truth,
        "tracking_recall": round(recall, 4),
        "track_associations": associations,
        "id_switches": id_switches,
        "id_switch_rate": round(switch_rate, 4),
        "association_accuracy": round(1.0 - switch_rate, 4),
        "standard_metric_note": (
            "These transparent diagnostics are not HOTA, MOTA, or IDF1. "
            "Use TrackEval for publication-standard tracking metrics."
        ),
    }


def evaluate_release_gate(
    detection_metrics: dict,
    event_metrics: dict | None = None,
    tracking_metrics: dict | None = None,
    thresholds: dict | None = None,
) -> dict:
    configured = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    per_class = detection_metrics.get("per_class", {})
    checks = {
        "player_recall": per_class.get("player", {}).get("recall", 0) >= configured["player_recall"],
        "ball_recall": per_class.get("ball", {}).get("recall", 0) >= configured["ball_recall"],
        "macro_f1": detection_metrics.get("overall", {}).get("macro_f1", 0) >= configured["macro_f1"],
    }
    if event_metrics is not None:
        checks.update({
            "event_precision": event_metrics.get("precision", 0) >= configured["event_precision"],
            "event_recall": event_metrics.get("recall", 0) >= configured["event_recall"],
        })
    if tracking_metrics is not None:
        checks.update({
            "tracking_recall": tracking_metrics.get("tracking_recall", 0)
            >= configured["tracking_recall"],
            "maximum_id_switch_rate": tracking_metrics.get("id_switch_rate", 1)
            <= configured["maximum_id_switch_rate"],
        })
    return {
        "approved": bool(checks) and all(checks.values()),
        "checks": checks,
        "thresholds": configured,
        "failed_checks": [name for name, passed in checks.items() if not passed],
    }
