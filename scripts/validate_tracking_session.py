#!/usr/bin/env python3
"""Validate a tracking session's telemetry against human ground truth.

    python scripts/validate_tracking_session.py --telemetry samples.json --ground-truth gt.json [--out report.json]

`samples.json` is a list of samples (or {"samples": [...]}), e.g. exported from
GET /tracking/sessions/{id}/samples. `gt.json` follows the format documented in
app/services/tracking_validation.py and ml/REAL_VIDEO_VALIDATION.md.
Prints a JSON report; it never prints a single "accuracy" number.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.tracking_validation import GroundTruthError, validate_session  # noqa: E402


def load_samples(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    samples = data.get("samples") if isinstance(data, dict) else data
    if not isinstance(samples, list):
        raise SystemExit(f"{path}: expected a list of samples or an object with a 'samples' list")
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--ground-truth", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--match-window", type=float, default=0.1, help="seconds")
    parser.add_argument("--ball-tolerance", type=float, default=0.04, help="normalized image units")
    arguments = parser.parse_args(argv)

    try:
        report = validate_session(
            load_samples(arguments.telemetry),
            json.loads(arguments.ground_truth.read_text()),
            match_window_seconds=arguments.match_window,
            ball_tolerance=arguments.ball_tolerance,
        )
    except GroundTruthError as error:
        print(f"Invalid ground truth: {error}", file=sys.stderr)
        return 2

    text = json.dumps(report, indent=2)
    if arguments.out:
        arguments.out.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
