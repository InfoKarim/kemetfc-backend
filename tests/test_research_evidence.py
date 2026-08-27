from app.research_evidence import build_research_evidence_report


def complete_inputs():
    return {
        "dataset_report": {"match_count": 10, "release_warnings": []},
        "detection_metrics": {
            "overall": {"macro_f1": 0.8},
            "per_class": {
                "player": {"recall": 0.9},
                "ball": {"recall": 0.8},
            },
        },
        "event_metrics": {"precision": 0.8, "recall": 0.7},
        "tracking_metrics": {"tracking_recall": 0.8, "id_switch_rate": 0.05},
        "annotation_agreement": {"agreement_f1": 0.9},
        "calibration_metrics": {
            "expected_calibration_error": 0.05,
            "brier_score": 0.1,
        },
        "slice_metrics": {"warnings": [], "maximum_accuracy_gap": 0.1},
        "baseline_comparison": {"statistically_clear_improvement": True},
        "ablation_report": {"variants": [{"name": "without_tracker"}]},
        "experiment_metadata": {
            "experiment_id": "EXP1",
            "git_revision": "abc123",
            "dataset_fingerprint": "fingerprint",
            "model_name": "football-yolo",
            "model_version": "1.0",
            "seed": 42,
        },
    }


def test_complete_evidence_can_become_release_candidate():
    report = build_research_evidence_report(**complete_inputs())

    assert report["research_release_candidate"] is True
    assert report["evidence_completion_percent"] == 100
    assert report["passed_check_percent"] == 100
    assert report["missing_evidence"] == []


def test_missing_evidence_is_not_counted_as_success():
    report = build_research_evidence_report(
        experiment_metadata=complete_inputs()["experiment_metadata"]
    )

    assert report["research_release_candidate"] is False
    assert report["status"] == "incomplete_evidence"
    assert "dataset_validated" in report["missing_evidence"]
    assert report["passed_check_percent"] == 12.5


def test_failed_calibration_blocks_release():
    inputs = complete_inputs()
    inputs["calibration_metrics"] = {
        "expected_calibration_error": 0.3,
        "brier_score": 0.4,
    }

    report = build_research_evidence_report(**inputs)

    assert report["research_release_candidate"] is False
    assert report["status"] == "evaluated_not_approved"
    assert report["failed_checks"] == ["confidence_calibrated"]


def test_missing_reproducibility_fields_are_reported():
    inputs = complete_inputs()
    inputs["experiment_metadata"] = {"experiment_id": "EXP1"}

    report = build_research_evidence_report(**inputs)
    metadata_check = next(
        item for item in report["checks"]
        if item["name"] == "reproducibility_metadata"
    )

    assert metadata_check["passed"] is False
    assert "git_revision" in metadata_check["evidence"]["missing_fields"]
