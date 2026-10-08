"""Versioned pose-sequence datasets: A1 evidence -> windows -> validated examples -> a fingerprinted dataset.

Only coach-selected, identity-LOCKED, consented evidence becomes an example. A window never spans a frame that is not
LOCKED, never spans a picture cut or a change of selection, and never contains a time gap. Keypoints are never
interpolated: a joint that was not usable is masked, and the mask is part of the input.
Raw videos never enter a dataset or Git: an example holds numbers (normalized pose, angles, motion) and opaque ids.
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.ml1 import consent as consent_rules
from app.ml1 import registry_adapter
from app.ml1.config import ANGLE_SCALE, ANGLES, DATASET_FORMAT, KEYPOINTS, MOTION, SCHEMA_VERSION, SPLITS, SYNTHETIC_SOURCE, WINDOW

ARRAYS = ("keypoints", "keypoint_mask", "angles", "angle_mask", "motion", "motion_mask", "t")


class DatasetError(ValueError):
    pass


class ExampleValidationError(DatasetError):
    pass


class InsufficientGroupsError(DatasetError):
    pass


# ---- A1 evidence -> windows -----------------------------------------------------------------------------------------

def _lock_key(frame: dict):
    ident = frame["identity"]
    if ident["state"] != "LOCKED" or ident.get("picture_cut"):
        return None
    return (ident.get("epoch"), ident.get("segment"))


def locked_runs(frames: list[dict]) -> list[list[int]]:
    """Runs of consecutive frame indices that are LOCKED in one epoch and one segment with no picture cut inside.
    A picture cut ends a run (the cut frame itself is never part of one)."""
    runs: list[list[int]] = []
    current: list[int] = []
    key = None
    for i, frame in enumerate(frames):
        k = _lock_key(frame)
        contiguous = current and frames[current[-1]]["frame_index"] + 1 == frame["frame_index"]
        if k is not None and k == key and contiguous and frame["timestamp_s"] > frames[current[-1]]["timestamp_s"]:
            current.append(i)
        else:
            if current:
                runs.append(current)
            current, key = ([i], k) if k is not None else ([], None)
    if current:
        runs.append(current)
    return runs


def _frame_arrays(frame: dict, aspect_w: float | None = None):
    kp = np.zeros((len(KEYPOINTS), 3), dtype=np.float32)
    kp_mask = np.zeros(len(KEYPOINTS), dtype=bool)
    landmarks = (frame.get("pose") or {}).get("landmarks")
    if landmarks:
        hips = [landmarks[n] for n in ("left_hip", "right_hip") if landmarks[n]["usable"]]
        shoulders = [landmarks[n] for n in ("left_shoulder", "right_shoulder") if landmarks[n]["usable"]]
        if len(hips) == 2 and len(shoulders) == 2:
            cx = sum(h["x_px"] for h in hips) / 2
            cy = sum(h["y_px"] for h in hips) / 2
            sx = sum(s["x_px"] for s in shoulders) / 2
            sy = sum(s["y_px"] for s in shoulders) / 2
            scale = math.hypot(sx - cx, sy - cy)
            if scale > 1e-6:
                for k, name in enumerate(KEYPOINTS):
                    m = landmarks[name]
                    if m["usable"]:
                        kp[k] = ((m["x_px"] - cx) / scale, (m["y_px"] - cy) / scale, m["visibility"])
                        kp_mask[k] = True
    ang = np.zeros(len(ANGLES), dtype=np.float32)
    ang_mask = np.zeros(len(ANGLES), dtype=bool)
    for a, name in enumerate(ANGLES):
        entry = frame["angles"].get(name)
        if entry and entry["state"] == "MEASURED" and entry["value"] is not None:
            ang[a] = entry["value"] / ANGLE_SCALE[entry["unit"]]
            ang_mask[a] = True
    return kp, kp_mask, ang, ang_mask


def windows_from_a1(frames: list[dict], fps: float, *, window: dict | None = None) -> list[dict]:
    """Candidate windows as {"indices": [...frame positions...], "run": n}, taken only inside unbroken LOCKED runs."""
    w = {**WINDOW, **(window or {})}
    stride = max(1, round(fps / w["target_hz"]))
    out = []
    for n, run in enumerate(locked_runs(frames)):
        sampled = run[::stride]
        length, hop = w["length_frames"], w["hop_frames"]
        for start in range(0, len(sampled) - length + 1, hop):
            idx = sampled[start:start + length]
            times = [frames[i]["timestamp_s"] for i in idx]
            if any(b - a > w["max_timestamp_gap_s"] or b <= a for a, b in zip(times, times[1:])):
                continue
            out.append({"indices": idx, "run": n})
    return out


def example_from_window(frames: list[dict], window: dict, *, meta_base: dict, example_id: str) -> tuple[dict, dict]:
    idx = window["indices"]
    T = len(idx)
    arrays = {"keypoints": np.zeros((T, len(KEYPOINTS), 3), np.float32), "keypoint_mask": np.zeros((T, len(KEYPOINTS)), bool),
              "angles": np.zeros((T, len(ANGLES)), np.float32), "angle_mask": np.zeros((T, len(ANGLES)), bool),
              "motion": np.zeros((T, len(MOTION)), np.float32), "motion_mask": np.zeros((T, len(MOTION)), bool),
              "t": np.array([frames[i]["timestamp_s"] for i in idx], np.float64)}
    track_conf, key_conf, observed = [], [], 0
    for r, i in enumerate(idx):
        f = frames[i]
        kp, kpm, ang, angm = _frame_arrays(f)
        arrays["keypoints"][r], arrays["keypoint_mask"][r], arrays["angles"][r], arrays["angle_mask"][r] = kp, kpm, ang, angm
        tc = (f.get("target") or {}).get("tracking_confidence")
        arrays["motion"][r, 2], arrays["motion_mask"][r, 2] = (tc or 0.0), tc is not None
        arrays["motion"][r, 3], arrays["motion_mask"][r, 3] = float(bool(f["camera"].get("camera_moving"))), True
        if r > 0:
            a, b = frames[idx[r - 1]], f
            ca, cb = (a.get("motion_proxy") or {}).get("body_center"), (b.get("motion_proxy") or {}).get("body_center")
            dt = b["timestamp_s"] - a["timestamp_s"]
            if ca and cb and dt > 0:
                arrays["motion"][r, 0] = (cb["x_frame_heights"] - ca["x_frame_heights"]) / dt
                arrays["motion"][r, 1] = (cb["y_frame_heights"] - ca["y_frame_heights"]) / dt
                arrays["motion_mask"][r, 0] = arrays["motion_mask"][r, 1] = True
        if tc is not None:
            track_conf.append(tc)
        if kpm.sum() >= 6:
            observed += 1
            key_conf.append(float(kp[kpm, 2].mean()))
    meta = json.loads(json.dumps(meta_base))
    meta.update({
        "schema_version": SCHEMA_VERSION, "example_id": example_id,
        "window": {"start_frame": frames[idx[0]]["frame_index"], "end_frame": frames[idx[-1]]["frame_index"],
                   "start_s": frames[idx[0]]["timestamp_s"], "end_s": frames[idx[-1]]["timestamp_s"], "frames": T,
                   "epoch": frames[idx[0]]["identity"].get("epoch"), "segment": frames[idx[0]]["identity"].get("segment")},
        "model_confidence": {"mean_tracking_confidence": round(float(np.mean(track_conf)), 4) if track_conf else None,
                             "mean_keypoint_confidence": round(float(np.mean(key_conf)), 4) if key_conf else None,
                             "observed_fraction": round(observed / T, 4)},
    })
    return meta, arrays


def build_from_a1(frames: list[dict], fps: float, *, meta_base: dict, id_prefix: str, window: dict | None = None) -> list[tuple[dict, dict]]:
    """All valid examples of one A1 run. Windows with too little observed pose are dropped, never padded."""
    w = {**WINDOW, **(window or {})}
    examples = []
    for n, win in enumerate(windows_from_a1(frames, fps, window=window)):
        meta, arrays = example_from_window(frames, win, meta_base=meta_base, example_id=f"{id_prefix}-{n:04d}")
        if meta["model_confidence"]["observed_fraction"] < w["min_observed_fraction"]:
            continue
        examples.append((meta, arrays))
    return examples


# ---- validation -------------------------------------------------------------------------------------------------------

def validate_arrays(arrays: dict, *, window: dict | None = None) -> None:
    w = {**WINDOW, **(window or {})}
    missing = [k for k in ARRAYS if k not in arrays]
    if missing:
        raise ExampleValidationError(f"missing arrays: {missing}")
    t = np.asarray(arrays["t"])
    T = len(t)
    shapes = {"keypoints": (T, len(KEYPOINTS), 3), "keypoint_mask": (T, len(KEYPOINTS)), "angles": (T, len(ANGLES)),
              "angle_mask": (T, len(ANGLES)), "motion": (T, len(MOTION)), "motion_mask": (T, len(MOTION))}
    for name, shape in shapes.items():
        if tuple(arrays[name].shape) != shape:
            raise ExampleValidationError(f"{name} has shape {tuple(arrays[name].shape)}, expected {shape}")
    for name in ("keypoint_mask", "angle_mask", "motion_mask"):
        if arrays[name].dtype != bool:
            raise ExampleValidationError(f"{name} must be boolean")
    for name in ("keypoints", "angles", "motion", "t"):
        if not np.all(np.isfinite(arrays[name])):
            raise ExampleValidationError(f"{name} contains NaN or infinity")
    if T < 2 or np.any(np.diff(t) <= 0):
        raise ExampleValidationError("timestamps must be strictly increasing")
    if np.any(np.diff(t) > w["max_timestamp_gap_s"] + 1e-9):
        raise ExampleValidationError("a gap between consecutive timestamps is too long: the window is not continuous")
    kp = arrays["keypoints"]
    if np.any((kp[..., 2] < 0) | (kp[..., 2] > 1)):
        raise ExampleValidationError("keypoint confidence must be in [0, 1]")
    if np.any(np.abs(kp[..., :2]) > 12):
        raise ExampleValidationError("a keypoint is implausibly far from the body (torso-scaled coordinates)")
    # a masked-out entry must be exactly zero: missing data is never filled in
    for values, mask in ((kp, arrays["keypoint_mask"]), (arrays["angles"], arrays["angle_mask"]), (arrays["motion"], arrays["motion_mask"])):
        hidden = values[~mask] if values.ndim == 2 else values[~mask]
        if np.any(hidden != 0):
            raise ExampleValidationError("a masked (missing) value is not zero: missing data must never be filled in")
    if np.any(np.abs(arrays["angles"][arrays["angle_mask"]]) > 4):
        raise ExampleValidationError("an angle is outside any plausible range")


def validate_meta(meta: dict, *, now: datetime | None = None, withdrawn_refs: set[str] | None = None, registry=None) -> None:
    """Shape and consent checks. For REAL data the consent claim inside `meta` is not evidence: the trusted registry
    must confirm it now (and a missing or unreachable registry is a refusal)."""
    for key in ("example_id", "anonymous_player_id", "assessment_id", "video_id", "drill_type", "target", "window",
                "ball_interaction", "labels", "model_confidence", "consent", "provenance"):
        if key not in meta:
            raise ExampleValidationError(f"missing field {key}")
    when = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    problems = consent_rules.eligibility_problems(meta, when, withdrawn_refs)
    problems += registry_adapter.registry_problems(meta, registry, when)
    if problems:
        raise consent_rules.ConsentError(f"example {meta['example_id']} is not eligible for learning: {', '.join(sorted(set(problems)))}")


# ---- identity-safety audit (independent of how the example was made) -----------------------------------------------------------

def audit_against_a1(meta: dict, a1_frames: list[dict]) -> list[str]:
    """Recheck an example's window against the A1 frames it claims to come from: every frame LOCKED in one epoch
    and segment, no picture cut, frame numbers contiguous."""
    w = meta["window"]
    span = [f for f in a1_frames if w["start_frame"] <= f["frame_index"] <= w["end_frame"]]
    problems = []
    if not span:
        return ["WINDOW_NOT_IN_SOURCE"]
    if any(f["identity"]["state"] != "LOCKED" for f in span):
        problems.append("WINDOW_CONTAINS_IDENTITY_UNSAFE_FRAMES")
    if any(f["identity"].get("picture_cut") for f in span):
        problems.append("WINDOW_CONTAINS_A_PICTURE_CUT")
    if len({(f["identity"].get("epoch"), f["identity"].get("segment")) for f in span}) > 1:
        problems.append("WINDOW_SPANS_SEGMENTS")
    return problems


# ---- the dataset ----------------------------------------------------------------------------------------------------------

def canonical(meta: dict) -> str:
    return json.dumps(meta, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(examples: list[tuple[dict, dict]]) -> str:
    digest = hashlib.sha256()
    for meta, arrays in sorted(examples, key=lambda e: e[0]["example_id"]):
        digest.update(canonical(meta).encode())
        for name in ARRAYS:
            a = np.ascontiguousarray(arrays[name])
            digest.update(name.encode() + str(a.dtype).encode() + str(a.shape).encode() + a.tobytes())
    return digest.hexdigest()


def build_dataset(examples: list[tuple[dict, dict]], *, version: str, now: datetime | None = None,
                  withdrawn_refs: set[str] | None = None, registry=None) -> dict:
    """Validate every example and refuse the whole dataset if any is ineligible or invalid. A dataset is either all
    real (consented, confirmed by the trusted registry) or all synthetic (each PROVEN to be the generator's output),
    never mixed."""
    from app.ml1 import synthetic
    if not examples:
        raise DatasetError("a dataset needs at least one example")
    ids = [m["example_id"] for m, _ in examples]
    if len(set(ids)) != len(ids):
        raise DatasetError("duplicate example ids")
    sources = {m["provenance"]["source"] for m, _ in examples}
    synthetic_only = SYNTHETIC_SOURCE in sources
    if synthetic_only and len(sources) > 1:
        raise DatasetError("a dataset must be entirely synthetic or entirely real; synthetic fixtures may not be mixed with real examples")
    for meta, arrays in examples:
        validate_meta(meta, now=now, withdrawn_refs=withdrawn_refs, registry=registry)
        if synthetic_only:
            proof = synthetic.verify_synthetic(meta, arrays)
            if proof:
                raise consent_rules.ConsentError(f"example {meta['example_id']} claims to be synthetic but is not: {', '.join(proof)}")
        validate_arrays(arrays)
    lineage = [] if synthetic_only else sorted({(m["video_id"], str(m["provenance"].get("source_video_sha256", "")).lower()) for m, _ in examples})
    return {"format": DATASET_FORMAT, "schema_version": SCHEMA_VERSION, "version": version, "fingerprint": fingerprint(examples),
            "synthetic_only": synthetic_only, "examples": examples, "keypoints": KEYPOINTS, "angles": ANGLES, "motion": MOTION,
            "coverage_lineage": [{"video_id": v, "video_sha256": h} for v, h in lineage]}


def save_dataset(directory: Path, dataset: dict) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {k: dataset[k] for k in ("format", "schema_version", "version", "fingerprint", "synthetic_only", "keypoints", "angles", "motion")}
    manifest["examples"] = [m for m, _ in dataset["examples"]]
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True, default=str))
    np.savez_compressed(directory / "arrays.npz", **{f"{m['example_id']}::{name}": a[name] for m, a in dataset["examples"] for name in ARRAYS})
    return directory


def load_dataset(directory: Path, *, now: datetime | None = None, withdrawn_refs: set[str] | None = None, registry=None) -> dict:
    """Load and RE-VERIFY: the fingerprint must match and every example must still be eligible (a consent may have
    been withdrawn or may have expired since it was saved)."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    store = np.load(directory / "arrays.npz")
    examples = [(m, {name: store[f"{m['example_id']}::{name}"] for name in ARRAYS}) for m in manifest["examples"]]
    if fingerprint(examples) != manifest["fingerprint"]:
        raise DatasetError("the dataset fingerprint does not match its contents: it was changed after it was built")
    return build_dataset(examples, version=manifest["version"], now=now, withdrawn_refs=withdrawn_refs, registry=registry)


# ---- splits without leakage ------------------------------------------------------------------------------------------------------

def _components(examples: list[tuple[dict, dict]]) -> dict[int, list[int]]:
    """Examples that share a player OR a source video are one group: they must land in the same split."""
    parent = list(range(len(examples)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    owner: dict[tuple[str, str], int] = {}
    for i, (meta, _) in enumerate(examples):
        for key in (("player", meta["anonymous_player_id"]), ("video", meta["video_id"]), ("assessment", meta["assessment_id"])):
            if key in owner:
                parent[find(i)] = find(owner[key])
            else:
                owner[key] = i
    groups: dict[int, list[int]] = {}
    for i in range(len(examples)):
        groups.setdefault(find(i), []).append(i)
    return groups


def split_dataset(dataset: dict, *, seed: int, ratios: dict | None = None, min_groups: int | None = None) -> dict[str, list[int]]:
    """Deterministic split by independent group (player + source video components). Returns split name -> example
    indices. Refuses, rather than leaking, when there are too few independent groups to evaluate honestly."""
    from app.ml1.config import MIN_GROUPS_PER_SPLIT, MIN_GROUPS_TOTAL
    ratios = ratios or SPLITS
    examples = dataset["examples"]
    groups = _components(examples)
    need = max(min_groups if min_groups is not None else MIN_GROUPS_TOTAL, MIN_GROUPS_PER_SPLIT * len(ratios))
    if len(groups) < need:
        raise InsufficientGroupsError(f"{len(groups)} independent player/video groups; at least {need} are needed to split without leakage")
    order = sorted(groups.values(), key=lambda g: hashlib.sha256(f"{seed}:{min(examples[i][0]['example_id'] for i in g)}".encode()).hexdigest())
    total = len(examples)
    result: dict[str, list[int]] = {name: [] for name in ratios}
    group_counts = {name: 0 for name in ratios}
    # first make sure every split gets a group, then fill by largest deficit
    names = list(ratios)
    for name, group in zip(names, order):
        result[name].extend(group)
        group_counts[name] += 1
    for group in order[len(names):]:
        deficits = {n: ratios[n] * total - len(result[n]) for n in names}
        name = max(names, key=lambda n: (deficits[n], -names.index(n)))
        result[name].extend(group)
        group_counts[name] += 1
    for name in result:
        result[name].sort()
    check_no_leakage(dataset, result)
    return result


def check_no_leakage(dataset: dict, splits: dict[str, list[int]]) -> None:
    """Raise if any player, source video, assessment or example appears in two splits, or if two examples of one
    source video overlap in time across splits."""
    examples = dataset["examples"]
    seen = {"player": {}, "video": {}, "assessment": {}, "example": {}}
    for name, indices in splits.items():
        for i in indices:
            meta = examples[i][0]
            for kind, value in (("player", meta["anonymous_player_id"]), ("video", meta["video_id"]),
                                ("assessment", meta["assessment_id"]), ("example", meta["example_id"])):
                if value in seen[kind] and seen[kind][value] != name:
                    raise DatasetError(f"data leakage: {kind} {value} is in both '{seen[kind][value]}' and '{name}'")
                seen[kind][value] = name
    covered = sorted(i for indices in splits.values() for i in indices)
    if covered != list(range(len(examples))):
        raise DatasetError("the splits do not cover every example exactly once")
