"""Evaluate two independent football annotation exports."""

import argparse
import json
from pathlib import Path

from app.annotation_agreement import (
    evaluate_detection_agreement,
    evaluate_event_agreement,
)


def _read(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"{path} must contain a JSON list")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detections-a", type=Path)
    parser.add_argument("--detections-b", type=Path)
    parser.add_argument("--events-a", type=Path)
    parser.add_argument("--events-b", type=Path)
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument("--event-tolerance-seconds", type=float, default=1.0)
    parser.add_argument("--minimum-f1", type=float, default=0.8)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ml/reports/annotation_agreement.json"),
    )
    args = parser.parse_args()
    if bool(args.detections_a) != bool(args.detections_b):
        parser.error("Both detection annotation files are required together")
    if bool(args.events_a) != bool(args.events_b):
        parser.error("Both event annotation files are required together")
    if not args.detections_a and not args.events_a:
        parser.error("Supply a detection pair, an event pair, or both")

    report = {}
    if args.detections_a:
        report["detections"] = evaluate_detection_agreement(
            _read(args.detections_a),
            _read(args.detections_b),
            args.iou_threshold,
            args.minimum_f1,
        )
    if args.events_a:
        report["events"] = evaluate_event_agreement(
            _read(args.events_a),
            _read(args.events_b),
            args.event_tolerance_seconds,
            args.minimum_f1,
        )
    report["passed"] = all(item["passed"] for item in report.values())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"passed={report['passed']}")
    print(f"report={args.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
