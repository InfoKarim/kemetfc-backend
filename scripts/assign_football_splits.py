"""Create deterministic match-level train/validation/test assignments."""

import argparse
import csv
from pathlib import Path

from app.football_dataset import assign_match_splits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--train-ratio", type=float, default=0.70)
    parser.add_argument("--val-ratio", type=float, default=0.15)
    args = parser.parse_args()
    with args.input.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if "split" not in fieldnames:
        fieldnames.append("split")
    assigned = assign_match_splits(rows, args.seed, args.train_ratio, args.val_ratio)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(assigned)
    print(f"rows={len(assigned)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
