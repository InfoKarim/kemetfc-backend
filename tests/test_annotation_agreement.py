import pytest

from app.annotation_agreement import (
    evaluate_detection_agreement,
    evaluate_event_agreement,
)


def test_detection_agreement_is_symmetric_and_reports_class_quality():
    first = [
        {"image_id": "I1", "class_name": "player", "bbox": [0, 0, 0.4, 0.8]},
        {"image_id": "I1", "class_name": "ball", "bbox": [0.5, 0.5, 0.6, 0.6]},
    ]
    second = [
        {"image_id": "I1", "class_name": "player", "bbox": [0, 0, 0.4, 0.8]},
        {"image_id": "I1", "class_name": "ball", "bbox": [0.8, 0.8, 0.9, 0.9]},
    ]

    report = evaluate_detection_agreement(first, second, minimum_f1=0.7)
    reverse = evaluate_detection_agreement(second, first, minimum_f1=0.7)

    assert report["agreement_f1"] == 0.5
    assert report["agreement_f1"] == reverse["agreement_f1"]
    assert report["per_class"]["player"]["agreement_f1"] == 1
    assert report["per_class"]["ball"]["agreement_f1"] == 0
    assert report["passed"] is False


def test_event_agreement_reports_timing_error():
    first = [{"event_type": "pass", "timestamp_seconds": 10.0}]
    second = [{"event_type": "pass", "timestamp_seconds": 10.4}]

    report = evaluate_event_agreement(first, second, tolerance_seconds=0.5)

    assert report["agreement_f1"] == 1
    assert report["mean_absolute_timing_error_seconds"] == 0.4
    assert report["passed"] is True


@pytest.mark.parametrize("threshold", [0, 1.1])
def test_detection_agreement_validates_threshold(threshold):
    with pytest.raises(ValueError, match="iou_threshold"):
        evaluate_detection_agreement([], [], iou_threshold=threshold)
