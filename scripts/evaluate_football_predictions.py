"""Evaluate exported football detections/events without requiring a GPU."""

import argparse
import json
from pathlib import Path

from app.ml_evaluation import (
    evaluate_detections,
    evaluate_events,
    evaluate_release_gate,
    evaluate_tracking,
)


def _read_json(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"Expected a JSON list in {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--event-ground-truth", type=Path)
    parser.add_argument("--event-predictions", type=Path)
    parser.add_argument("--tracking-ground-truth", type=Path)
    parser.add_argument("--tracking-predictions", type=Path)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--event-tolerance", type=float, default=1.0)
    parser.add_argument("--output", type=Path, default=Path("ml/reports/evaluation.json"))
    args = parser.parse_args()

    detection = evaluate_detections(
        _read_json(args.ground_truth), _read_json(args.predictions), args.iou
    )
    events = None
    if bool(args.event_ground_truth) != bool(args.event_predictions):
        parser.error("Both event files must be supplied together")
    if args.event_ground_truth:
        events = evaluate_events(
            _read_json(args.event_ground_truth),
            _read_json(args.event_predictions),
            args.event_tolerance,
        )
    tracking = None
    if bool(args.tracking_ground_truth) != bool(args.tracking_predictions):
        parser.error("Both tracking files must be supplied together")
    if args.tracking_ground_truth:
        tracking = evaluate_tracking(
            _read_json(args.tracking_ground_truth),
            _read_json(args.tracking_predictions),
            args.iou,
        )
    report = {
        "schema_version": "1.0",
        "detection": detection,
        "events": events,
        "tracking": tracking,
        "release_gate": evaluate_release_gate(detection, events, tracking),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"release_approved={str(report['release_gate']['approved']).lower()}")
    print(f"report={args.output}")
    return 0 if report["release_gate"]["approved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
