"""Validate a football dataset manifest and write a reproducible report."""

import argparse
import json
from pathlib import Path

from app.football_dataset import DatasetValidationError, validate_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path("datasets/football/manifest.csv"))
    parser.add_argument("--dataset-root", type=Path, default=Path("datasets/football"))
    parser.add_argument("--output", type=Path, default=Path("ml/reports/dataset_validation.json"))
    args = parser.parse_args()
    try:
        report = validate_dataset(args.manifest, args.dataset_root)
    except DatasetValidationError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"dataset_fingerprint={report['dataset_fingerprint']}")
    print(f"samples={report['sample_count']} matches={report['match_count']}")
    print(f"report={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
