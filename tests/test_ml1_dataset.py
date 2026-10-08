"""ML-1 datasets: validation, missing keypoints, bad timestamps, identity-unsafe frames, scene cuts, leakage."""
import copy
import json

import numpy as np
import pytest

from app.ml1 import dataset as ds
from app.ml1.config import ANGLES, KEYPOINTS, SCHEMA_VERSION
from app.ml1.synthetic import make_synthetic_examples
from tests.ml1_helpers import NOW, a1_frame, a1_run, real_examples, real_meta


def fresh():
    return copy.deepcopy(make_synthetic_examples(6, 1, 2, seed=1)[0][1])


# ---- validation ---------------------------------------------------------------------------------------------------------
def test_a_clean_example_validates():
    ds.validate_arrays(fresh())


@pytest.mark.parametrize("name", ["keypoints", "keypoint_mask", "angles", "angle_mask", "motion", "motion_mask", "t"])
def test_a_missing_array_is_refused(name):
    a = fresh()
    del a[name]
    with pytest.raises(ds.ExampleValidationError, match="missing arrays"):
        ds.validate_arrays(a)


def test_wrong_shapes_nan_and_bad_dtypes_are_refused():
    a = fresh(); a["keypoints"] = a["keypoints"][:, :5]
    with pytest.raises(ds.ExampleValidationError, match="shape"):
        ds.validate_arrays(a)
    a = fresh(); a["keypoints"][3, 2, 0] = np.nan
    with pytest.raises(ds.ExampleValidationError, match="NaN"):
        ds.validate_arrays(a)
    a = fresh(); a["keypoint_mask"] = a["keypoint_mask"].astype(np.int8)
    with pytest.raises(ds.ExampleValidationError, match="boolean"):
        ds.validate_arrays(a)
    a = fresh(); a["keypoints"][0, 0, 2] = 1.5
    with pytest.raises(ds.ExampleValidationError, match="confidence"):
        ds.validate_arrays(a)
    a = fresh(); a["keypoints"][0, 0, 0] = 50.0
    with pytest.raises(ds.ExampleValidationError, match="implausibly"):
        ds.validate_arrays(a)


# ---- missing keypoints are never filled in ------------------------------------------------------------------------------------
def test_a_missing_keypoint_must_be_zero_with_a_zero_mask_nothing_is_filled_in():
    a = fresh()
    r, k = np.argwhere(~a["keypoint_mask"])[0]
    a["keypoints"][r, k, :2] = 0.7                     # a value invented for a joint that was never seen
    with pytest.raises(ds.ExampleValidationError, match="never be filled in"):
        ds.validate_arrays(a)
    a = fresh()
    r, k = np.argwhere(~a["angle_mask"])[0]
    a["angles"][r, k] = 0.5
    with pytest.raises(ds.ExampleValidationError, match="never be filled in"):
        ds.validate_arrays(a)


def test_unusable_joints_from_a1_become_masked_zeros_and_are_not_interpolated():
    frames = a1_run(400)
    for f in frames[100:140]:
        f["pose"]["landmarks"]["left_ankle"]["usable"] = False
    examples = ds.build_from_a1(frames, 60.0, meta_base=real_meta(), id_prefix="T")
    assert examples
    k = KEYPOINTS.index("left_ankle")
    hit = 0
    for meta, a in examples:
        ds.validate_arrays(a)
        missing = ~a["keypoint_mask"][:, k]
        if missing.any():
            hit += 1
            assert np.all(a["keypoints"][missing, k, :] == 0)
    assert hit >= 1


def test_a_frame_with_no_pose_is_masked_and_a_window_without_enough_pose_is_dropped_not_padded():
    frames = a1_run(400)
    for f in frames:
        f["pose"]["landmarks"] = None
        for a in f["angles"].values():
            a.update(value=None, state="NOT_MEASURABLE")
    assert ds.build_from_a1(frames, 60.0, meta_base=real_meta(), id_prefix="T") == []


# ---- timestamps ---------------------------------------------------------------------------------------------------------------
def test_timestamps_must_strictly_increase_and_have_no_long_gap():
    a = fresh(); a["t"][10] = a["t"][9]
    with pytest.raises(ds.ExampleValidationError, match="strictly increasing"):
        ds.validate_arrays(a)
    a = fresh(); a["t"][10] = a["t"][9] - 0.01
    with pytest.raises(ds.ExampleValidationError, match="strictly increasing"):
        ds.validate_arrays(a)
    a = fresh(); a["t"][20:] += 5.0
    with pytest.raises(ds.ExampleValidationError, match="gap"):
        ds.validate_arrays(a)
    a = fresh(); a["t"][3] = np.inf
    with pytest.raises(ds.ExampleValidationError, match="NaN or infinity"):
        ds.validate_arrays(a)


def test_a_window_is_never_made_across_a_timestamp_gap_in_the_source():
    frames = a1_run(1200)
    for f in frames[600:]:
        f["timestamp_s"] += 3.0                         # the source clock jumps (a dropped chunk)
    for meta, a in ds.build_from_a1(frames, 60.0, meta_base=real_meta(), id_prefix="T"):
        assert np.all(np.diff(a["t"]) <= 0.25 + 1e-9)


# ---- identity-unsafe frames and scene cuts --------------------------------------------------------------------------------------
def test_no_window_ever_contains_a_frame_that_is_not_locked():
    frames = a1_run(1500)
    for f in frames[500:560]:
        f.update(identity={"state": "UNCERTAIN", "segment": None, "epoch": 1, "picture_cut": False}, target=None)
    for f in frames[900:960]:
        f.update(identity={"state": "LOST", "segment": None, "epoch": 1, "picture_cut": False}, target=None)
    for f in frames[1300:]:
        f.update(identity={"state": "RESELECTION_REQUIRED", "segment": None, "epoch": 1, "picture_cut": False}, target=None)
    examples = ds.build_from_a1(frames, 60.0, meta_base=real_meta(), id_prefix="T")
    assert examples
    for meta, _ in examples:
        w = meta["window"]
        assert not [f for f in frames[w["start_frame"]:w["end_frame"] + 1] if f["identity"]["state"] != "LOCKED"]
        assert ds.audit_against_a1(meta, frames) == []


def test_a_tampered_example_that_spans_unsafe_frames_is_caught_by_the_audit():
    frames = a1_run(600)
    for f in frames[300:320]:
        f["identity"]["state"] = "UNCERTAIN"
    meta, _ = ds.build_from_a1(a1_run(600), 60.0, meta_base=real_meta(), id_prefix="T")[0]
    meta["window"].update(start_frame=250, end_frame=400)
    assert "WINDOW_CONTAINS_IDENTITY_UNSAFE_FRAMES" in ds.audit_against_a1(meta, frames)
    assert ds.audit_against_a1({"window": {"start_frame": 9000, "end_frame": 9100}}, frames) == ["WINDOW_NOT_IN_SOURCE"]


def test_a_picture_cut_ends_a_run_and_no_window_crosses_it():
    frames = a1_run(1500)
    frames[700]["identity"]["picture_cut"] = True
    runs = ds.locked_runs(frames)
    assert all(700 not in r for r in runs) and any(r[-1] == 699 for r in runs) and any(r[0] == 701 for r in runs)
    for meta, _ in ds.build_from_a1(frames, 60.0, meta_base=real_meta(), id_prefix="T"):
        w = meta["window"]
        assert not (w["start_frame"] <= 700 <= w["end_frame"])
        assert ds.audit_against_a1(meta, frames) == []
    cut_inside = a1_run(600)
    cut_inside[200]["identity"]["picture_cut"] = True
    meta, _ = ds.build_from_a1(a1_run(600), 60.0, meta_base=real_meta(), id_prefix="T")[0]
    meta["window"].update(start_frame=100, end_frame=300)
    assert "WINDOW_CONTAINS_A_PICTURE_CUT" in ds.audit_against_a1(meta, cut_inside)


def test_a_change_of_selection_or_segment_ends_a_run():
    frames = a1_run(1000)
    for f in frames[500:]:
        f["identity"].update(segment=2, epoch=2)
    for meta, _ in ds.build_from_a1(frames, 60.0, meta_base=real_meta(), id_prefix="T"):
        w = meta["window"]
        assert not (w["start_frame"] < 500 <= w["end_frame"])


def test_normalization_uses_the_body_so_the_same_pose_anywhere_in_the_frame_is_the_same_example():
    f1, f2 = a1_frame(0), a1_frame(0)
    for m in f2["pose"]["landmarks"].values():
        m["x_px"] += 200; m["y_px"] += 150
    k1, m1, _, _ = ds._frame_arrays(f1)
    k2, m2, _, _ = ds._frame_arrays(f2)
    assert m1.all() and m2.all() and np.allclose(k1, k2, atol=1e-5)
    f3 = a1_frame(0)
    f3["pose"]["landmarks"]["left_hip"]["usable"] = False        # no reference body: nothing is normalized, nothing invented
    _, m3, _, _ = ds._frame_arrays(f3)
    assert not m3.any()


# ---- datasets: fingerprint, mixing, saving ---------------------------------------------------------------------------------------
def test_a_dataset_is_fingerprinted_and_a_changed_file_is_detected(tmp_path):
    d = ds.build_dataset(make_synthetic_examples(6, 1, 2), version="s1")
    assert d["fingerprint"] == ds.build_dataset(make_synthetic_examples(6, 1, 2), version="s1")["fingerprint"]
    ds.save_dataset(tmp_path / "d", d)
    assert ds.load_dataset(tmp_path / "d")["fingerprint"] == d["fingerprint"]
    manifest = json.loads((tmp_path / "d/manifest.json").read_text())
    manifest["examples"][0]["drill_type"] = "SOMETHING_ELSE"
    (tmp_path / "d/manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ds.DatasetError, match="fingerprint"):
        ds.load_dataset(tmp_path / "d")


def test_synthetic_and_real_examples_are_never_mixed_and_duplicates_are_refused():
    mixed = make_synthetic_examples(2, 1, 1) + real_examples(2, 1, 1)
    with pytest.raises(ds.DatasetError, match="entirely synthetic or entirely real"):
        ds.build_dataset(mixed, version="m")
    one = make_synthetic_examples(2, 1, 1)
    with pytest.raises(ds.DatasetError, match="duplicate"):
        ds.build_dataset(one + [one[0]], version="d")
    with pytest.raises(ds.DatasetError, match="at least one"):
        ds.build_dataset([], version="e")


def test_no_raw_video_path_or_pixels_are_stored_in_a_dataset(tmp_path):
    d = ds.build_dataset(make_synthetic_examples(3, 1, 1), version="s")
    ds.save_dataset(tmp_path / "d", d)
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["arrays.npz", "manifest.json"]
    text = (tmp_path / "d/manifest.json").read_text().lower()
    for word in (".mov", ".mp4", "/users/", "filepath", "video_path"):
        assert word not in text


# ---- splits and leakage ---------------------------------------------------------------------------------------------------------
def test_splits_keep_every_player_and_every_source_video_in_exactly_one_split():
    d = ds.build_dataset(make_synthetic_examples(12, 2, 4), version="s")
    sp = ds.split_dataset(d, seed=5)
    ex = d["examples"]
    owner = {}
    for name, idx in sp.items():
        for i in idx:
            for key in (("p", ex[i][0]["anonymous_player_id"]), ("v", ex[i][0]["video_id"]), ("a", ex[i][0]["assessment_id"])):
                assert owner.setdefault(key, name) == name
    assert sorted(i for v in sp.values() for i in v) == list(range(len(ex)))
    assert all(sp.values())


def test_two_players_sharing_one_video_are_one_group_and_cannot_be_split_apart():
    ex = make_synthetic_examples(8, 1, 3)
    shared = ex[0][0]["video_id"]
    for meta, _ in ex[3:6]:                                  # a second player appears in the first player's video
        meta["video_id"] = shared
    d = ds.build_dataset(ex, version="s")
    sp = ds.split_dataset(d, seed=2, min_groups=3)
    where = {name for name, idx in sp.items() for i in idx if d["examples"][i][0]["video_id"] == shared}
    assert len(where) == 1


def test_a_manual_leak_is_detected():
    d = ds.build_dataset(make_synthetic_examples(10, 1, 3), version="s")
    sp = ds.split_dataset(d, seed=1)
    leaked = {k: list(v) for k, v in sp.items()}
    some = d["examples"][sp["train"][0]][0]["anonymous_player_id"]
    other = next(i for i in sp["test"])
    d["examples"][other][0]["anonymous_player_id"] = some       # the same player now also in test
    with pytest.raises(ds.DatasetError, match="data leakage"):
        ds.check_no_leakage(d, leaked)
    with pytest.raises(ds.DatasetError, match="cover every example"):
        ds.check_no_leakage(ds.build_dataset(make_synthetic_examples(10, 1, 3), version="s"), {"train": [0], "val": [], "test": []})


def test_too_few_independent_groups_refuses_to_split_instead_of_leaking():
    d = ds.build_dataset(make_synthetic_examples(2, 1, 5), version="s")
    with pytest.raises(ds.InsufficientGroupsError):
        ds.split_dataset(d, seed=1)


def test_the_split_is_deterministic_and_changes_with_the_seed():
    d = ds.build_dataset(make_synthetic_examples(14, 2, 2), version="s")
    assert ds.split_dataset(d, seed=9) == ds.split_dataset(d, seed=9)
    assert ds.split_dataset(d, seed=9) != ds.split_dataset(d, seed=10)
