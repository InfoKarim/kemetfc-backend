"""Compose auditable evidence into a conservative ML research readiness decision."""

from __future__ import annotations

from datetime import datetime, timezone

from app.ml_evaluation import evaluate_release_gate


DEFAULT_RESEARCH_THRESHOLDS = {
    "minimum_annotation_agreement_f1": 0.8,
    "maximum_expected_calibration_error": 0.1,
    "maximum_brier_score": 0.2,
    "maximum_slice_accuracy_gap": 0.2,
}


def _check(name: str, passed: bool | None, evidence=None) -> dict:
    return {"name": name, "passed": passed, "evidence": evidence}


def build_research_evidence_report(
    *,
    dataset_report: dict | None = None,
    detection_metrics: dict | None = None,
    event_metrics: dict | None = None,
    tracking_metrics: dict | None = None,
    annotation_agreement: dict | None = None,
    calibration_metrics: dict | None = None,
    slice_metrics: dict | None = None,
    baseline_comparison: dict | None = None,
    ablation_report: dict | None = None,
    experiment_metadata: dict | None = None,
    research_thresholds: dict | None = None,
    release_thresholds: dict | None = None,
) -> dict:
    """Build one versioned report without treating absent evidence as success."""
    thresholds = {
        **DEFAULT_RESEARCH_THRESHOLDS,
        **(research_thresholds or {}),
    }
    checks = []

    checks.append(_check(
        "dataset_validated",
        None if dataset_report is None else (
            dataset_report.get("match_count", 0) >= 3
            and not dataset_report.get("release_warnings")
        ),
        dataset_report,
    ))
    checks.append(_check(
        "annotation_agreement",
        None if annotation_agreement is None else (
            annotation_agreement.get("agreement_f1", 0)
            >= thresholds["minimum_annotation_agreement_f1"]
        ),
        annotation_agreement,
    ))

    ml_gate = None
    if detection_metrics is not None:
        ml_gate = evaluate_release_gate(
            detection_metrics,
            event_metrics,
            tracking_metrics,
            release_thresholds,
        )
    checks.append(_check(
        "task_metrics_release_gate",
        None if ml_gate is None else ml_gate["approved"],
        ml_gate,
    ))
    checks.append(_check(
        "confidence_calibrated",
        None if calibration_metrics is None else (
            calibration_metrics.get("expected_calibration_error", 1)
            <= thresholds["maximum_expected_calibration_error"]
            and calibration_metrics.get("brier_score", 1)
            <= thresholds["maximum_brier_score"]
        ),
        calibration_metrics,
    ))
    checks.append(_check(
        "deployment_slices_evaluated",
        None if slice_metrics is None else (
            not slice_metrics.get("warnings")
            and slice_metrics.get("maximum_accuracy_gap", 1)
            <= thresholds["maximum_slice_accuracy_gap"]
        ),
        slice_metrics,
    ))
    checks.append(_check(
        "paired_baseline_comparison",
        None if baseline_comparison is None else baseline_comparison.get(
            "statistically_clear_improvement", False
        ),
        baseline_comparison,
    ))
    checks.append(_check(
        "ablation_completed",
        None if ablation_report is None else bool(ablation_report.get("variants")),
        ablation_report,
    ))

    metadata = experiment_metadata or {}
    required_metadata = {
        "experiment_id",
        "git_revision",
        "dataset_fingerprint",
        "model_name",
        "model_version",
        "seed",
    }
    missing_metadata = sorted(required_metadata - set(metadata))
    checks.append(_check(
        "reproducibility_metadata",
        False if missing_metadata else True,
        {"metadata": metadata, "missing_fields": missing_metadata},
    ))

    evaluated = [item for item in checks if item["passed"] is not None]
    passed = [item for item in evaluated if item["passed"]]
    failed = [item for item in evaluated if not item["passed"]]
    missing = [item["name"] for item in checks if item["passed"] is None]
    completion = len(evaluated) / len(checks)
    pass_rate = len(passed) / len(checks)
    release_candidate = not missing and not failed
    if release_candidate:
        status = "research_release_candidate"
    elif completion == 1:
        status = "evaluated_not_approved"
    else:
        status = "incomplete_evidence"

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "research_release_candidate": release_candidate,
        "evidence_completion_percent": round(completion * 100, 1),
        "passed_check_percent": round(pass_rate * 100, 1),
        "thresholds": thresholds,
        "checks": checks,
        "failed_checks": [item["name"] for item in failed],
        "missing_evidence": missing,
        "decision_note": (
            "A release-candidate result means the supplied evidence passed the "
            "configured gates. It does not replace independent ethical, privacy, "
            "security, licensing, or field-pilot review."
        ),
    }
