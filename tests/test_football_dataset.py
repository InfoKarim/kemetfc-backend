import csv
from pathlib import Path

import pytest

from app.football_dataset import DatasetValidationError, assign_match_splits, validate_dataset


FIELDS = [
    "image_path", "label_path", "match_id", "club_id", "camera_id",
    "age_band", "sex", "lighting", "consent_id", "split",
]


def build_dataset(tmp_path: Path, split_matches=None):
    split_matches = split_matches or {"train": "M1", "val": "M2", "test": "M3"}
    rows = []
    for index, (split, match_id) in enumerate(split_matches.items()):
        image = tmp_path / f"image_{index}.jpg"
        label = tmp_path / f"image_{index}.txt"
        image.write_bytes(f"image-{index}".encode())
        label.write_text("0 0.5 0.5 0.2 0.4\n3 0.2 0.3 0.02 0.02\n")
        rows.append({
            "image_path": image.name, "label_path": label.name, "match_id": match_id,
            "club_id": "CLUB", "camera_id": f"CAM{index % 2}", "age_band": "U13",
            "sex": "mixed", "lighting": "day" if index < 2 else "night",
            "consent_id": f"CONSENT{index}", "split": split,
        })
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return manifest


def test_validate_dataset_reports_lineage_and_strata(tmp_path):
    manifest = build_dataset(tmp_path)

    report = validate_dataset(manifest, tmp_path)

    assert report["sample_count"] == 3
    assert report["match_count"] == 3
    assert report["samples_by_split"] == {"test": 1, "train": 1, "val": 1}
    assert report["annotations_by_class"] == {"ball": 3, "player": 3}
    assert len(report["dataset_fingerprint"]) == 64
    assert report["release_warnings"] == []


def test_validate_dataset_rejects_match_leakage(tmp_path):
    manifest = build_dataset(tmp_path, {"train": "SAME", "val": "SAME", "test": "M3"})

    with pytest.raises(DatasetValidationError, match="Match leakage"):
        validate_dataset(manifest, tmp_path)


def test_validate_dataset_rejects_invalid_yolo_coordinates(tmp_path):
    manifest = build_dataset(tmp_path)
    (tmp_path / "image_0.txt").write_text("3 1.5 0.5 0.1 0.1\n")

    with pytest.raises(DatasetValidationError, match="normalized"):
        validate_dataset(manifest, tmp_path)


def test_assign_match_splits_is_deterministic_and_keeps_matches_together():
    rows = [
        {"match_id": match_id, "frame": frame}
        for match_id in ("M1", "M2", "M3", "M4", "M5", "M6")
        for frame in (1, 2)
    ]

    first = assign_match_splits(rows, seed=7)
    second = assign_match_splits(rows, seed=7)

    assert first == second
    assert {row["split"] for row in first} == {"train", "val", "test"}
    for match_id in {row["match_id"] for row in first}:
        assert len({row["split"] for row in first if row["match_id"] == match_id}) == 1


def test_assign_match_splits_requires_three_matches():
    with pytest.raises(DatasetValidationError, match="three matches"):
        assign_match_splits([{"match_id": "M1"}, {"match_id": "M2"}])
