"""Measures a tracking session's telemetry against HUMAN ground truth.

This is the entry point for the real-video validation step: record (or
replay) a clip, annotate what is actually in it, export the session's
samples, and this module computes how the system did — selected-player
retention, false identity switches, reacquisition, ball precision/recall,
pose association and telemetry continuity.

HONESTY RULES
  * It reports COUNTS with their denominators, and a ratio only when its
    denominator is non-zero (otherwise None). It never rounds a missing
    measurement into a zero or invents a percentage.
  * The numbers are only as trustworthy as the ground truth: the report
    echoes the ground truth's own `metadata` and labels itself
    `evidence: "real_video"` ONLY when that metadata says
    `source == "real_video"`. Synthetic or unlabeled ground truth is
    reported as such — this module cannot turn a test fixture into a claim
    about real footage.
  * Pure and DB-free: plain dicts in, plain dicts out.

GROUND TRUTH FORMAT (JSON)
  {
    "metadata": {"source": "real_video", "clip": "...", "annotator": "...", "notes": "..."},
    "frames": [
      {"t_seconds": 12.0,
       "target_visible": true,
       "target_bbox": [x, y, w, h],          # normalized top-left origin; required when visible
       "ball_visible": true,
       "ball_center": [x, y]}                 # required when ball_visible
    ]
  }
"""

from __future__ import annotations

import math

from app.services.tracking_features import POSE_BOX_PADDING, POSE_MIN_INSIDE_FRACTION, POSE_MIN_JOINTS
from app.services.tracking_quality import TRACKED_STATUSES

# A telemetry sample is matched to the ground-truth frame nearest in time, but
# only if it is within this many seconds.
DEFAULT_MATCH_WINDOW_SECONDS = 0.1
# A tracked position counts as "on the target" within this fraction of the
# ground-truth box's larger side of its center (the box must be judged by its
# own size, since the frame is normalized and players are small).
DEFAULT_TARGET_TOLERANCE_BOX_FRACTIONS = 0.75
DEFAULT_BALL_TOLERANCE = 0.04
# A loss must last at least this many matched frames to count as a loss (single-frame dropouts are noise).
DEFAULT_MIN_LOSS_FRAMES = 2


class GroundTruthError(ValueError):
    pass


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _center(box: list[float]) -> list[float]:
    return [box[0] + box[2] / 2, box[1] + box[3] / 2]


def _distance(a: list[float], b: list[float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def validate_ground_truth(ground_truth: dict) -> list[dict]:
    """The annotated frames, or GroundTruthError describing what is wrong."""
    frames = ground_truth.get("frames") if isinstance(ground_truth, dict) else None
    if not isinstance(frames, list) or not frames:
        raise GroundTruthError("ground truth needs a non-empty 'frames' list")
    for index, frame in enumerate(frames):
        if not isinstance(frame.get("t_seconds"), (int, float)):
            raise GroundTruthError(f"frame {index}: 't_seconds' must be a number")
        if frame.get("target_visible") and not (isinstance(frame.get("target_bbox"), list) and len(frame["target_bbox"]) == 4):
            raise GroundTruthError(f"frame {index}: target_visible requires a 4-value 'target_bbox'")
        if frame.get("ball_visible") and not (isinstance(frame.get("ball_center"), list) and len(frame["ball_center"]) == 2):
            raise GroundTruthError(f"frame {index}: ball_visible requires a 2-value 'ball_center'")
    return sorted(frames, key=lambda f: f["t_seconds"])


def _match(samples: list[dict], frames: list[dict], window: float) -> list[tuple[dict, dict]]:
    """(ground-truth frame, nearest telemetry sample within the window) for every annotated frame."""
    ordered = sorted(samples, key=lambda s: s["t_seconds"])
    pairs: list[tuple[dict, dict]] = []
    cursor = 0
    for frame in frames:
        while cursor + 1 < len(ordered) and abs(ordered[cursor + 1]["t_seconds"] - frame["t_seconds"]) <= abs(
            ordered[cursor]["t_seconds"] - frame["t_seconds"]
        ):
            cursor += 1
        if ordered and abs(ordered[cursor]["t_seconds"] - frame["t_seconds"]) <= window:
            pairs.append((frame, ordered[cursor]))
    return pairs


def _on_target(sample: dict, frame: dict, tolerance_fraction: float) -> bool:
    center = sample.get("player_center")
    box = frame.get("target_bbox")
    if center is None or not box:
        return False
    return _distance(center, _center(box)) <= tolerance_fraction * max(box[2], box[3])


def _is_tracked(sample: dict) -> bool:
    return sample.get("tracking_status") in TRACKED_STATUSES and sample.get("player_center") is not None


def _player_metrics(pairs, tolerance_fraction: float, min_loss_frames: int) -> dict:
    visible = [(f, s) for f, s in pairs if f.get("target_visible")]
    correct = wrong = missing = 0
    wrong_runs = 0
    in_wrong_run = False
    ambiguous_while_correct = 0
    for frame, sample in visible:
        if _is_tracked(sample):
            if _on_target(sample, frame, tolerance_fraction):
                correct += 1
                in_wrong_run = False
                if sample.get("identity_ambiguous") is True:
                    ambiguous_while_correct += 1
            else:
                wrong += 1
                if not in_wrong_run:
                    wrong_runs += 1
                in_wrong_run = True
        else:
            missing += 1
            in_wrong_run = False

    # Reacquisition: a loss is >= min_loss_frames consecutive visible frames with no tracked sample.
    events: list[dict] = []
    run_start: dict | None = None
    run_frames = 0
    for frame, sample in visible:
        if not _is_tracked(sample):
            if run_start is None:
                run_start = frame
            run_frames += 1
            continue
        if run_start is not None and run_frames >= min_loss_frames:
            events.append({
                "lost_at": run_start["t_seconds"],
                "reacquired_at": frame["t_seconds"],
                "latency_seconds": frame["t_seconds"] - run_start["t_seconds"],
                "correct": _on_target(sample, frame, tolerance_fraction),
            })
        run_start, run_frames = None, 0
    still_lost = run_start is not None and run_frames >= min_loss_frames

    return {
        "target_visible_frames": len(visible),
        "tracked_on_target_frames": correct,
        "tracked_wrong_person_frames": wrong,
        "not_tracked_frames": missing,
        "selected_player_retention": _ratio(correct, len(visible)),
        "false_id_switch_frame_ratio": _ratio(wrong, len(visible)),
        "false_id_switch_events": wrong_runs,
        "on_target_frames_flagged_ambiguous": ambiguous_while_correct,
        "reacquisition_events": events,
        "correct_reacquisitions": sum(1 for e in events if e["correct"]),
        "false_reacquisitions": sum(1 for e in events if not e["correct"]),
        "still_lost_at_end": still_lost,
    }


def _ball_metrics(pairs, tolerance: float) -> dict:
    tp = fp = fn = tn = 0
    wrong_location = 0
    id_changes = 0
    previous_id = None
    for frame, sample in pairs:
        detected = sample.get("ball_center") is not None and (sample.get("ball_confidence") or 0) > 0
        if frame.get("ball_visible"):
            if detected and _distance(sample["ball_center"], frame["ball_center"]) <= tolerance:
                tp += 1
            elif detected:
                fp += 1
                fn += 1
                wrong_location += 1
            else:
                fn += 1
        elif detected:
            fp += 1
        else:
            tn += 1
        track_id = sample.get("ball_track_id") if detected else None
        if track_id is not None and previous_id is not None and track_id != previous_id and frame.get("ball_visible"):
            id_changes += 1
        if track_id is not None:
            previous_id = track_id
    return {
        "frames": len(pairs),
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "true_negatives": tn,
        "detections_at_wrong_location": wrong_location,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "ball_track_id_changes_while_visible": id_changes,
        "tolerance_normalized": tolerance,
    }


def _pose_metrics(pairs) -> dict:
    on_target = wrong = unverifiable = 0
    missing_while_visible = 0
    for frame, sample in pairs:
        pose = sample.get("pose_keypoints") or []
        visible = bool(frame.get("target_visible"))
        if not pose:
            if visible:
                missing_while_visible += 1
            continue
        box = frame.get("target_bbox")
        if not visible or not box or len(pose) < POSE_MIN_JOINTS:
            unverifiable += 1
            continue
        x, y, w, h = box
        pad_x, pad_y = w * POSE_BOX_PADDING, h * POSE_BOX_PADDING
        inside = sum(1 for j in pose if x - pad_x <= j.get("x", -1) <= x + w + pad_x and y - pad_y <= j.get("y", -1) <= y + h + pad_y)
        if inside / len(pose) >= POSE_MIN_INSIDE_FRACTION:
            on_target += 1
        else:
            wrong += 1
    with_pose = on_target + wrong
    return {
        "frames_with_pose_and_target_visible": with_pose,
        "pose_on_target_frames": on_target,
        "pose_on_wrong_person_frames": wrong,
        "pose_unverifiable_frames": unverifiable,
        "pose_absent_while_target_visible_frames": missing_while_visible,
        "pose_association_precision": _ratio(on_target, with_pose),
    }


def telemetry_continuity(samples: list[dict]) -> dict:
    """Gaps, duplicates and ordering problems in the telemetry itself (no ground truth needed)."""
    ordered = sorted(samples, key=lambda s: s["t_seconds"])
    sequences = [s["sequence_number"] for s in samples if s.get("sequence_number") is not None]
    unique = sorted(set(sequences))
    gaps = ((unique[-1] - unique[0] + 1) - len(unique)) if len(unique) > 1 else 0
    times = [s["t_seconds"] for s in ordered]
    deltas = [b - a for a, b in zip(times, times[1:])]
    by_sequence = sorted((s for s in samples if s.get("sequence_number") is not None), key=lambda s: s["sequence_number"])
    order_violations = sum(1 for a, b in zip(by_sequence, by_sequence[1:]) if b["t_seconds"] < a["t_seconds"])
    duration = (times[-1] - times[0]) if len(times) > 1 else 0.0
    return {
        "sample_count": len(samples),
        "sequenced_sample_count": len(sequences),
        "missing_sequence_numbers": gaps,
        "duplicate_sequence_numbers": len(sequences) - len(unique),
        "sequence_vs_time_order_violations": order_violations,
        "max_time_gap_seconds": max(deltas) if deltas else None,
        "effective_sample_rate_hz": (len(samples) - 1) / duration if duration > 0 else None,
    }


def validate_session(
    samples: list[dict],
    ground_truth: dict,
    *,
    match_window_seconds: float = DEFAULT_MATCH_WINDOW_SECONDS,
    target_tolerance_box_fraction: float = DEFAULT_TARGET_TOLERANCE_BOX_FRACTIONS,
    ball_tolerance: float = DEFAULT_BALL_TOLERANCE,
    min_loss_frames: int = DEFAULT_MIN_LOSS_FRAMES,
) -> dict:
    frames = validate_ground_truth(ground_truth)
    metadata = ground_truth.get("metadata") or {}
    pairs = _match(samples, frames, match_window_seconds)
    is_real = metadata.get("source") == "real_video"
    return {
        "evidence": "real_video" if is_real else "not_real_video",
        "ground_truth_metadata": metadata,
        "parameters": {
            "match_window_seconds": match_window_seconds,
            "target_tolerance_box_fraction": target_tolerance_box_fraction,
            "ball_tolerance_normalized": ball_tolerance,
            "min_loss_frames": min_loss_frames,
        },
        "coverage": {
            "annotated_frames": len(frames),
            "matched_frames": len(pairs),
            "unmatched_frames": len(frames) - len(pairs),
        },
        "player": _player_metrics(pairs, target_tolerance_box_fraction, min_loss_frames),
        "ball": _ball_metrics(pairs, ball_tolerance),
        "pose": _pose_metrics(pairs),
        "telemetry": telemetry_continuity(samples),
        "notes": [
            "Counts come with denominators; a ratio is None when its denominator is 0.",
            "Metrics are only as valid as the ground-truth annotations and cover only the annotated frames.",
            "This report is NOT a claim about real footage unless evidence == 'real_video'.",
        ],
    }
