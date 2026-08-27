import pytest

from app.ml_evaluation import (
    evaluate_detections,
    evaluate_events,
    evaluate_release_gate,
    evaluate_tracking,
    intersection_over_union,
)


def box(image_id, class_name, bounds, confidence=None):
    value = {"image_id": image_id, "class_name": class_name, "bbox": bounds}
    if confidence is not None:
        value["confidence"] = confidence
    return value


def test_iou_handles_overlap_and_disjoint_boxes():
    assert intersection_over_union([0, 0, 1, 1], [0, 0, 1, 1]) == 1
    assert intersection_over_union([0, 0, 0.5, 0.5], [0.5, 0.5, 1, 1]) == 0


def test_detection_evaluation_reports_class_metrics_and_errors():
    truth = [
        box("A", "player", [0, 0, 0.4, 0.8]),
        box("A", "ball", [0.5, 0.5, 0.6, 0.6]),
    ]
    predictions = [
        box("A", "player", [0, 0, 0.4, 0.8], 0.9),
        box("A", "ball", [0.8, 0.8, 0.9, 0.9], 0.8),
    ]

    report = evaluate_detections(truth, predictions)

    assert report["per_class"]["player"]["recall"] == 1
    assert report["per_class"]["ball"]["recall"] == 0
    assert report["error_count"] == 2


def test_event_evaluation_uses_timestamp_tolerance():
    truth = [{"event_type": "pass", "timestamp_seconds": 10.0}]
    predictions = [{"event_type": "pass", "timestamp_seconds": 10.4}]

    report = evaluate_events(truth, predictions, tolerance_seconds=0.5)

    assert report["precision"] == 1
    assert report["recall"] == 1


def test_release_gate_fails_without_ball_quality():
    metrics = {
        "overall": {"macro_f1": 0.8},
        "per_class": {"player": {"recall": 0.9}, "ball": {"recall": 0.2}},
    }

    gate = evaluate_release_gate(metrics)

    assert gate["approved"] is False
    assert gate["failed_checks"] == ["ball_recall"]


def test_detection_evaluation_validates_iou():
    with pytest.raises(ValueError, match="iou_threshold"):
        evaluate_detections([], [], 0)


def test_tracking_evaluation_detects_identity_switch():
    truth = [
        {"frame_id": 1, "track_id": 10, "class_name": "player", "bbox": [0, 0, 1, 1]},
        {"frame_id": 2, "track_id": 10, "class_name": "player", "bbox": [0, 0, 1, 1]},
    ]
    predictions = [
        {"frame_id": 1, "track_id": 20, "class_name": "player", "bbox": [0, 0, 1, 1]},
        {"frame_id": 2, "track_id": 21, "class_name": "player", "bbox": [0, 0, 1, 1]},
    ]

    report = evaluate_tracking(truth, predictions)

    assert report["tracking_recall"] == 1
    assert report["id_switches"] == 1
    assert report["id_switch_rate"] == 1
    assert "not HOTA" in report["standard_metric_note"]
