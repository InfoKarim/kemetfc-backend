"""Validation and lineage helpers for consented football datasets."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


FOOTBALL_CLASSES = {0: "player", 1: "goalkeeper", 2: "referee", 3: "ball"}
REQUIRED_COLUMNS = {
    "image_path",
    "label_path",
    "match_id",
    "club_id",
    "camera_id",
    "age_band",
    "sex",
    "lighting",
    "consent_id",
    "split",
}
VALID_SPLITS = {"train", "val", "test"}


class DatasetValidationError(ValueError):
    pass


@dataclass(frozen=True)
class FootballSample:
    image_path: str
    label_path: str
    match_id: str
    club_id: str
    camera_id: str
    age_band: str
    sex: str
    lighting: str
    consent_id: str
    split: str


def load_manifest(path: Path) -> list[FootballSample]:
    path = Path(path)
    if not path.is_file():
        raise DatasetValidationError(f"Manifest not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise DatasetValidationError(
                f"Manifest is missing columns: {', '.join(sorted(missing))}"
            )
        samples = [FootballSample(**{key: row[key].strip() for key in REQUIRED_COLUMNS})
                   for row in reader]
    if not samples:
        raise DatasetValidationError("Manifest contains no samples")
    return samples


def _resolve(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    root = root.resolve()
    if candidate != root and root not in candidate.parents:
        raise DatasetValidationError(f"Dataset path escapes its root: {value}")
    return candidate


def _validate_label(path: Path) -> Counter:
    counts = Counter()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            raise DatasetValidationError(f"{path}:{line_number}: expected 5 YOLO values")
        try:
            class_id = int(parts[0])
            coordinates = [float(value) for value in parts[1:]]
        except ValueError as error:
            raise DatasetValidationError(f"{path}:{line_number}: invalid numeric value") from error
        if class_id not in FOOTBALL_CLASSES:
            raise DatasetValidationError(f"{path}:{line_number}: unsupported class {class_id}")
        if any(value < 0 or value > 1 for value in coordinates):
            raise DatasetValidationError(
                f"{path}:{line_number}: coordinates must be normalized to 0..1"
            )
        if coordinates[2] <= 0 or coordinates[3] <= 0:
            raise DatasetValidationError(f"{path}:{line_number}: box dimensions must be positive")
        counts[FOOTBALL_CLASSES[class_id]] += 1
    return counts


def validate_dataset(manifest_path: Path, dataset_root: Path) -> dict:
    """Validate files, consent metadata, labels, and match-level split isolation."""
    samples = load_manifest(manifest_path)
    root = Path(dataset_root)
    split_matches = defaultdict(set)
    split_samples = Counter()
    class_counts = Counter()
    strata = {name: Counter() for name in ("club_id", "camera_id", "age_band", "sex", "lighting")}
    seen_images = set()
    fingerprint = hashlib.sha256()

    for index, sample in enumerate(samples, 2):
        if sample.split not in VALID_SPLITS:
            raise DatasetValidationError(f"Manifest row {index}: invalid split {sample.split!r}")
        for field in ("match_id", "club_id", "camera_id", "age_band", "sex", "lighting"):
            if not getattr(sample, field):
                raise DatasetValidationError(f"Manifest row {index}: {field} cannot be empty")
        if not sample.consent_id:
            raise DatasetValidationError(f"Manifest row {index}: consent_id is required")
        image_path = _resolve(root, sample.image_path)
        label_path = _resolve(root, sample.label_path)
        if not image_path.is_file() or not label_path.is_file():
            raise DatasetValidationError(f"Manifest row {index}: image or label file is missing")
        if image_path in seen_images:
            raise DatasetValidationError(f"Duplicate image in manifest: {sample.image_path}")
        seen_images.add(image_path)
        split_matches[sample.split].add(sample.match_id)
        split_samples[sample.split] += 1
        class_counts.update(_validate_label(label_path))
        for field, values in strata.items():
            values[getattr(sample, field)] += 1
        fingerprint.update(json.dumps(asdict(sample), sort_keys=True).encode())
        fingerprint.update(hashlib.sha256(image_path.read_bytes()).digest())
        fingerprint.update(hashlib.sha256(label_path.read_bytes()).digest())

    split_names = sorted(split_matches)
    for position, first in enumerate(split_names):
        for second in split_names[position + 1:]:
            overlap = split_matches[first] & split_matches[second]
            if overlap:
                raise DatasetValidationError(
                    f"Match leakage between {first} and {second}: {', '.join(sorted(overlap))}"
                )
    missing_splits = VALID_SPLITS - set(split_samples)
    if missing_splits:
        raise DatasetValidationError(f"Dataset is missing splits: {', '.join(sorted(missing_splits))}")

    return {
        "schema_version": "1.0",
        "dataset_fingerprint": fingerprint.hexdigest(),
        "sample_count": len(samples),
        "match_count": len({sample.match_id for sample in samples}),
        "samples_by_split": dict(sorted(split_samples.items())),
        "matches_by_split": {key: len(value) for key, value in sorted(split_matches.items())},
        "annotations_by_class": dict(sorted(class_counts.items())),
        "strata": {key: dict(sorted(value.items())) for key, value in strata.items()},
        "release_warnings": [
            message for condition, message in (
                (class_counts["ball"] == 0, "No ball annotations were found."),
                (len(strata["camera_id"]) < 2, "Only one camera setup is represented."),
                (len(strata["lighting"]) < 2, "Only one lighting condition is represented."),
            ) if condition
        ],
    }


def assign_match_splits(
    rows: list[dict],
    seed: int = 42,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> list[dict]:
    """Assign complete matches to deterministic splits without frame leakage."""
    if train_ratio <= 0 or val_ratio <= 0 or train_ratio + val_ratio >= 1:
        raise ValueError("Split ratios must be positive and leave room for test")
    matches = sorted({row.get("match_id", "").strip() for row in rows})
    if "" in matches:
        raise DatasetValidationError("match_id cannot be empty")
    if len(matches) < 3:
        raise DatasetValidationError("At least three matches are required for train/val/test")
    random.Random(seed).shuffle(matches)
    count = len(matches)
    train_count = max(1, min(count - 2, round(count * train_ratio)))
    remaining = count - train_count
    val_count = max(1, min(remaining - 1, round(count * val_ratio)))
    assignments = {
        match_id: (
            "train" if index < train_count
            else "val" if index < train_count + val_count
            else "test"
        )
        for index, match_id in enumerate(matches)
    }
    return [{**row, "split": assignments[row["match_id"].strip()]} for row in rows]
