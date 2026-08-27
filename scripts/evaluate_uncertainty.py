"""Evaluate confidence calibration, abstention, and cohort reliability."""

import argparse
import json
from pathlib import Path

from app.uncertainty_evaluation import (
    choose_abstention_policy,
    evaluate_calibration,
    evaluate_calibration_slices,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("records", type=Path)
    parser.add_argument("--bin-count", type=int, default=10)
    parser.add_argument("--slice-key", action="append", default=[])
    parser.add_argument("--minimum-slice-size", type=int, default=30)
    parser.add_argument("--maximum-error-rate", type=float, default=0.1)
    parser.add_argument("--minimum-coverage", type=float, default=0.5)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ml/reports/uncertainty.json"),
    )
    args = parser.parse_args()
    records = json.loads(args.records.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("records must contain a JSON list")
    report = {
        "calibration": evaluate_calibration(records, args.bin_count),
        "abstention_policy": choose_abstention_policy(
            records,
            args.maximum_error_rate,
            args.minimum_coverage,
        ),
        "slices": {
            key: evaluate_calibration_slices(
                records,
                key,
                args.bin_count,
                args.minimum_slice_size,
            )
            for key in args.slice_key
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"ece={report['calibration']['expected_calibration_error']}")
    print(f"abstention_policy_approved={report['abstention_policy']['approved']}")
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
