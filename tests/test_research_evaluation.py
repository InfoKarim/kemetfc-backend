import pytest

from app.research_evaluation import (
    evaluate_ablation_study,
    evaluate_performance_slices,
    paired_bootstrap_comparison,
    wilson_interval,
)


def test_slice_evaluation_reports_gaps_and_small_cohorts():
    records = [
        {"correct": True, "lighting": "day", "age_band": "U13"},
        {"correct": True, "lighting": "day", "age_band": "U13"},
        {"correct": False, "lighting": "night", "age_band": "U15"},
    ]

    report = evaluate_performance_slices(
        records,
        ["lighting", "age_band"],
        minimum_samples=2,
    )

    assert report["slices"]["lighting=day"]["accuracy"] == 1
    assert report["slices"]["lighting=night"]["accuracy"] == 0
    assert report["maximum_accuracy_gap"] == 1
    assert any("lighting=night" in warning for warning in report["warnings"])


def test_paired_bootstrap_uses_identical_examples_and_is_deterministic():
    baseline = [
        {"example_id": f"E{index}", "correct": index < 4}
        for index in range(10)
    ]
    candidate = [
        {"example_id": f"E{index}", "correct": index < 9}
        for index in range(10)
    ]

    first = paired_bootstrap_comparison(baseline, candidate, 500, seed=7)
    second = paired_bootstrap_comparison(baseline, candidate, 500, seed=7)

    assert first == second
    assert first["absolute_accuracy_difference"] == 0.5
    assert first["candidate_accuracy"] == 0.9
    assert first["bootstrap_probability_of_improvement"] > 0.95


def test_ablation_ranks_components_by_metric_drop():
    report = evaluate_ablation_study(
        [
            {"name": "full", "f1": 0.82},
            {"name": "without_tracking", "f1": 0.70},
            {"name": "without_calibration", "f1": 0.80},
        ],
        full_run_name="full",
        metric_name="f1",
    )

    assert report["variants"][0]["name"] == "without_tracking"
    assert report["variants"][0]["estimated_component_contribution"] == 0.12


def test_wilson_interval_contains_observed_rate():
    interval = wilson_interval(8, 10)

    assert interval[0] < 0.8 < interval[1]


def test_paired_bootstrap_rejects_unpaired_examples():
    with pytest.raises(ValueError, match="same non-empty examples"):
        paired_bootstrap_comparison(
            [{"example_id": "A", "correct": True}],
            [{"example_id": "B", "correct": True}],
            bootstrap_samples=100,
        )
