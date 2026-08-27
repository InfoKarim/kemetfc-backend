import pytest

from app.uncertainty_evaluation import (
    choose_abstention_policy,
    evaluate_calibration,
    evaluate_calibration_slices,
    selective_risk_curve,
)


def test_calibration_reports_reliability_and_proper_scores():
    records = [
        {"confidence": 0.9, "correct": True},
        {"confidence": 0.8, "correct": True},
        {"confidence": 0.2, "correct": False},
        {"confidence": 0.1, "correct": False},
    ]

    report = evaluate_calibration(records, bin_count=5)

    assert report["sample_count"] == 4
    assert report["expected_calibration_error"] == 0.15
    assert report["brier_score"] == 0.025
    assert len(report["reliability_bins"]) == 5


def test_abstention_policy_reduces_error_with_visible_coverage():
    records = [
        {"confidence": 0.95, "correct": True},
        {"confidence": 0.9, "correct": True},
        {"confidence": 0.6, "correct": False},
        {"confidence": 0.4, "correct": False},
    ]

    policy = choose_abstention_policy(
        records,
        maximum_error_rate=0,
        minimum_coverage=0.5,
    )

    assert policy["approved"] is True
    assert policy["selected"]["threshold"] == 0.9
    assert policy["selected"]["coverage"] == 0.5
    assert policy["selected"]["selective_risk"] == 0


def test_selective_curve_keeps_empty_threshold_explicit():
    curve = selective_risk_curve(
        [{"confidence": 0.5, "correct": True}],
        thresholds=[0, 1],
    )

    assert curve[0]["coverage"] == 1
    assert curve[1]["coverage"] == 0
    assert curve[1]["selective_risk"] is None


def test_slice_calibration_warns_when_evidence_is_small():
    report = evaluate_calibration_slices(
        [
            {"confidence": 0.8, "correct": True, "lighting": "day"},
            {"confidence": 0.4, "correct": False, "lighting": "night"},
        ],
        slice_key="lighting",
        bin_count=2,
        minimum_slice_size=2,
    )

    assert set(report["slices"]) == {"day", "night"}
    assert len(report["warnings"]) == 2


@pytest.mark.parametrize(
    "record, message",
    [
        ({"confidence": 1.2, "correct": True}, "confidence"),
        ({"confidence": 0.8, "correct": "yes"}, "correct"),
    ],
)
def test_calibration_rejects_invalid_records(record, message):
    with pytest.raises(ValueError, match=message):
        evaluate_calibration([record])
