"""Combine experiment artifacts into one auditable research evidence report."""

import argparse
import json
from pathlib import Path

from app.research_evidence import build_research_evidence_report


def _read(path: Path | None) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--detections", type=Path)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--tracking", type=Path)
    parser.add_argument("--agreement", type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--slices", type=Path)
    parser.add_argument("--baseline-comparison", type=Path)
    parser.add_argument("--ablation", type=Path)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ml/reports/research_evidence.json"),
    )
    args = parser.parse_args()
    report = build_research_evidence_report(
        dataset_report=_read(args.dataset),
        detection_metrics=_read(args.detections),
        event_metrics=_read(args.events),
        tracking_metrics=_read(args.tracking),
        annotation_agreement=_read(args.agreement),
        calibration_metrics=_read(args.calibration),
        slice_metrics=_read(args.slices),
        baseline_comparison=_read(args.baseline_comparison),
        ablation_report=_read(args.ablation),
        experiment_metadata=_read(args.metadata),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"status={report['status']}")
    print(f"evidence_completion_percent={report['evidence_completion_percent']}")
    print(f"report={args.output}")
    return 0 if report["research_release_candidate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
