"""Run A1 end to end and write the evidence artifact (JSON header + per-frame JSONL + short human summary).

A player assessment needs the coach's selection of the player in the video (target_selection). Without one the run
refuses, unless the clearly labelled DIAGNOSTIC fallback is asked for; that fallback is never a verified identity.
`identity_only` follows the target and reports identity evidence only: no pose, no angles, no motion proxies.
"""
import json
import platform
import sys
import time
from pathlib import Path

import cv2
import mediapipe
import torch
import ultralytics

from app.video_ai import geometry, identity, pipeline, validate
from app.video_ai import target_selection as ts, target_tracking as tt
from app.video_ai.config import (ANGLE_DEFINITION_VERSION, DETECTION, IDENTITY, PIPELINE_VERSION, POSE, PROXY,
                                 PROXY_DEFINITION_VERSION, SCHEMA_VERSION)

def _log(message):
    print(message, file=sys.stderr)


LABELS = "VIDEO_MOTION_PROXY = image-plane quantities (frame heights, degrees, hertz); not metres, not m/s"


def _not_measurable_runs(frames: list[dict]) -> dict:
    """Per measurement, runs of frames where it was NOT_MEASURABLE, grouped by the first part of the reason."""
    result = {}

    def collect(name, getter):
        labelled = []
        for f in frames:
            entry = getter(f)
            labelled.append((f["frame_index"], None if entry["state"] == "MEASURED"
                             else (entry["reason"] or "UNKNOWN").split(":")[0]))
        runs = tt_runs(labelled)
        for r in runs:
            r["start_s"], r["end_s"], r["reason"] = frames[r["start_frame"]]["timestamp_s"], \
                frames[r["end_frame"]]["timestamp_s"], r.pop("label")
        result[name] = runs

    for name in geometry.ANGLE_DEFINITIONS:
        collect(name, lambda f, n=name: f["angles"][n])
    collect("speed_proxy_frame_heights_per_s", lambda f: f["motion_proxy"]["speed_proxy_frame_heights_per_s"])
    collect("acceleration_proxy_frame_heights_per_s2",
            lambda f: f["motion_proxy"]["acceleration_proxy_frame_heights_per_s2"])
    return result


def tt_runs(labelled: list[tuple[int, str | None]]) -> list[dict]:
    result: list[dict] = []
    for index, label in labelled:
        if label is None:
            continue
        if result and result[-1]["label"] == label and result[-1]["end_frame"] == index - 1:
            result[-1]["end_frame"] = index
        else:
            result.append({"start_frame": index, "end_frame": index, "label": label})
    return result


def load_or_detect(video: Path, info: dict, model, cache: Path | None, sha: str, progress=_log) -> list[dict]:
    """Pass 1, or its saved result when the same video was already detected with the same settings."""
    key = {"video_sha256": sha, "detection": DETECTION, "pipeline_version": PIPELINE_VERSION}
    if cache and Path(cache).is_file():
        saved = json.loads(Path(cache).read_text())
        if saved.get("key") == key:
            return saved["records"]
    records = pipeline.detect_pass(video, info, model, lambda p, n: progress(f"{p}: {n} frames"))
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps({"key": key, "records": records}, separators=(",", ":")))
    return records


def suggest_target_frames(video: Path, cache: Path | None = None, count: int = 5, yolo_model: Path = pipeline.DEFAULT_YOLO,
                          pose_model: Path = pipeline.DEFAULT_POSE, progress=_log) -> list[dict]:
    """Frames worth showing the coach for SELECT PLAYER IN VIDEO (people visible, sharp, not a transition)."""
    video = Path(video)
    info = pipeline.probe_video(video)
    model = pipeline._load_models(yolo_model, pose_model)
    records = load_or_detect(video, info, model, cache, pipeline.sha256_file(video), progress)
    cuts = pipeline.picture_cuts(records)
    near = int(IDENTITY["suggest_clear_of_cut_seconds"] * info["average_fps"])
    cut_frames = [i for i, c in enumerate(cuts) if c]
    frames = [{"detections": r["detections"], "sharpness": r["sharpness"], "brightness": r["brightness"],
               "near_cut": any(abs(i - c) <= near for c in cut_frames)} for i, r in enumerate(records)]
    picks = ts.suggest_frames(frames, info["average_fps"], count)
    for pick in picks:
        i = pick["frame_index"]
        pick["timestamp_s"] = info["presentation_timestamps_s"][i]
        pick["detections"] = [{"index": k, "bbox_norm": d["bbox_norm"], "confidence": d["confidence"]}
                              for k, d in enumerate(records[i]["detections"])]
    return picks


def _diagnostic_selection(detect_records, info) -> ts.TargetSelection:
    picked = identity.select_most_prominent([r["detections"] for r in detect_records], info["average_fps"])
    if picked is None:
        raise pipeline.NoTargetSelected("no person was present long enough, even for the diagnostic fallback")
    frame_index, detection_index, _ = picked
    box = detect_records[frame_index]["detections"][detection_index]["bbox_norm"]
    tap = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return ts.TargetSelection(
        label="DIAGNOSTIC_MOST_PROMINENT", source=ts.SOURCE_DIAGNOSTIC_MOST_PROMINENT, frame_index=frame_index,
        timestamp_s=info["presentation_timestamps_s"][frame_index], tap_norm=tap, bbox_norm=tuple(box),
        detection_confidence=detect_records[frame_index]["detections"][detection_index]["confidence"],
        note="DIAGNOSTIC ONLY: the most prominent person, not a verified player identity")


def run_a1(video: Path, out_dir: Path, recording_id: str, player_id: str, selections: list[ts.TargetSelection] | None = None,
           *, diagnostic_most_prominent: bool = False, identity_only: bool = False, detections_cache: Path | None = None,
           yolo_model: Path = pipeline.DEFAULT_YOLO, pose_model: Path = pipeline.DEFAULT_POSE, progress=_log) -> dict:
    if not selections and not diagnostic_most_prominent:
        raise pipeline.NoTargetSelected("the coach must select the player in the video before the AI follows anyone")
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    sha_before = pipeline.sha256_file(video)
    info = pipeline.probe_video(video)
    model = pipeline._load_models(yolo_model, pose_model)

    detect_records = load_or_detect(video, info, model, detections_cache, sha_before, progress)
    if len(detect_records) != len(info["presentation_timestamps_s"]):
        raise pipeline.AnalysisError("frame count decoded differs from the container timestamps")
    cuts = pipeline.picture_cuts(detect_records)
    selections = list(selections or [])
    if not selections:
        selections = [_diagnostic_selection(detect_records, info)]
    resolved = pipeline.resolve_selections(detect_records, selections)
    aspect = info["display_width"] / info["display_height"]
    tracking = tt.track_selected([r["detections"] for r in detect_records], cuts, resolved, info["average_fps"], aspect)

    if identity_only:
        pose_records = pipeline.no_pose(len(detect_records), "IDENTITY_ONLY_RUN")
    else:
        boxes = [r["target"]["bbox_norm"] if r["state"] == tt.LOCKED and r["target"] else None for r in tracking]
        pose_records = pipeline.pose_pass(video, info, boxes, pose_model, lambda p, n: progress(f"{p}: {n} frames"))
    frames, extra = pipeline.assemble(info, detect_records, pose_records, tracking, cuts, identity_only)
    windows = extra["windows"]
    tracks = identity.track_summaries([r["detections"] for r in detect_records], info["average_fps"])
    summaries = pipeline._summaries(frames, windows, info, tracks)
    checks = validate.validate(frames, info["presentation_timestamps_s"], windows)

    frames_path = out_dir / "a1_frames.jsonl"
    with open(frames_path, "w") as handle:
        for f in frames:
            handle.write(json.dumps(f, separators=(",", ":"), sort_keys=True) + "\n")
    sha_after = pipeline.sha256_file(video)
    coach_verified = all(s.identity_verified_by_coach for s in selections)

    header = {
        "schema_version": SCHEMA_VERSION, "pipeline_version": PIPELINE_VERSION, "phase": "A1.5_TARGET_SELECTION_IDENTITY_SAFETY",
        "mode": "IDENTITY_ONLY_NO_BODY_ANALYSIS" if identity_only else "POSE_AND_BODY_MOVEMENT_ON_LOCKED_TARGET",
        "evidence_not_score": True, "labels": LABELS,
        "recording_id": recording_id, "player_id": player_id,
        "video": {"path": str(video), "sha256": sha_before, "sha256_after_analysis": sha_after,
                  "unchanged": sha_before == sha_after, **{k: v for k, v in info.items()
                                                           if k != "presentation_timestamps_s"},
                  "timestamp_source": "ffprobe best_effort_timestamp_time per decoded frame, checked 1:1 against "
                                      "the decoded frame count", "frame_coordinates": "upright display frame; "
                  "x,y in pixels of display_width x display_height, normalized 0..1 with origin top-left"},
        "models": {
            "person_detector": {"name": "Ultralytics YOLO", "weights": str(yolo_model),
                                "weights_sha256": pipeline.sha256_file(yolo_model),
                                "ultralytics": ultralytics.__version__, "torch": torch.__version__,
                                "classes_used": ["person"], "settings": DETECTION,
                                "note": "its own ByteTrack ids are recorded for diagnostics only; identity is "
                                        "decided by target_tracking from the coach's selection"},
            "pose": {"name": "MediaPipe Pose Landmarker (lite, 33 landmarks)", "weights": str(pose_model),
                     "weights_sha256": pipeline.sha256_file(pose_model), "mediapipe": mediapipe.__version__,
                     "mode": "IMAGE on the LOCKED target's box only" if not identity_only else "NOT RUN (identity-only run)",
                     "settings": POSE},
            "runtime": {"opencv": cv2.__version__, "python": platform.python_version(), "platform": platform.platform()},
        },
        "frame_sampling": {"method": "every decoded frame (stride 1)", "frames_processed": len(frames)},
        "target_selection": {
            "policy": "COACH_SELECTED" if coach_verified else "NOT_VERIFIED_BY_COACH",
            "identity_verified_by_coach": coach_verified,
            "selections": [s.as_json() for s in selections],
            "resolved_on_this_detector": [{"frame_index": f, "detection_index": d,
                                           "bbox_norm": detect_records[f]["detections"][d]["bbox_norm"],
                                           "detection_confidence": detect_records[f]["detections"][d]["confidence"]}
                                          for f, d in resolved],
            "tie_rule": "closest box centre (in the box's own size), then smaller box, then higher confidence, then lower index",
            "no_face_recognition": True,
            "roster_player_id_is_not_the_video_target": True,
            "note": ("the target is a temporary tracking identity for this video; the roster player id only says whose "
                     "assessment it is" if coach_verified else
                     "NOT a verified player identity: " + "; ".join(sorted({s.note or s.source for s in selections})))},
        "identity_policy": {"states": list(tt.STATES), "analysis_allowed_in": list(tt.ANALYSIS_ALLOWED_STATES),
                            "thresholds": IDENTITY,
                            "rules": ["a picture cut ends continuity (RESELECTION_REQUIRED)",
                                      "a target who left the frame, or was gone longer than the recovery window, is never "
                                      "re-adopted automatically", "an overlapping or look-alike neighbour makes recovery "
                                      "unprovable", "nothing recovers from RESELECTION_REQUIRED except a new coach selection",
                                      "measurements use only unbroken LOCKED runs inside one selection"]},
        "thresholds": {"identity": IDENTITY, "pose": POSE, "proxy": PROXY},
        "definitions": {"angle_definition_version": ANGLE_DEFINITION_VERSION,
                        "proxy_definition_version": PROXY_DEFINITION_VERSION,
                        "angles": geometry.ANGLE_DESCRIPTIONS, "core_joints": geometry.CORE_JOINTS,
                        "landmark_order": geometry.LANDMARK_NAMES,
                        "landmark_usable": "visibility and presence >= 0.5, inside the frame, inside the target box "
                                           "grown by 10%"},
        "per_frame_file": {"path": frames_path.name, "format": "JSON Lines, one object per frame",
                           "sha256": pipeline.sha256_file(frames_path)},
        "detector_tracks": sorted(tracks.values(), key=lambda t: t["track_id"]),
        "summary": summaries, "windows": windows,
        "not_measurable_intervals": _not_measurable_runs(frames),
        "validation": {"checks": checks, "all_passed": all(c["passed"] for c in checks)},
        "run": {"seconds": round(time.time() - started, 1)},
    }
    (out_dir / "a1_analysis.json").write_text(json.dumps(header, indent=1, sort_keys=True))
    (out_dir / "a1_summary.md").write_text(render_summary(header))
    return header


def render_summary(h: dict) -> str:
    s, v, t = h["summary"], h["video"], h["target_selection"]
    lines = [f"# Target identity and A1 evidence - recording {h['recording_id']}",
             "", "Evidence only. No score, no rating, no physical speed.", f"Mode: {h['mode']}", "",
             f"- assessment owner: {h['player_id']} (the roster player; not the same thing as the person followed)",
             f"- video: {v['display_width']}x{v['display_height']} upright, {v['frame_count_container']} frames, "
             f"{v['duration_s']:.2f} s, SHA-256 `{v['sha256'][:16]}...` unchanged: {v['unchanged']}",
             f"- target selection: {t['policy']}; verified by coach: {t['identity_verified_by_coach']}",
             "", "## Selections", ""]
    for sel in t["selections"]:
        lines.append(f"- {sel['label']} ({sel['source']}) at frame {sel['frame_index']}, {sel['timestamp_s']:.3f} s, "
                     f"tap {sel['tap_norm']}, box {sel['bbox_norm']}, detection confidence {sel['detection_confidence']}")
    lines += ["", "## Identity states (all frames)", ""]
    for state, n in s["identity_state_counts"].items():
        lines.append(f"- {state}: {n} frames ({s['identity_state_percent'][state]}%)")
    lines += ["", f"Picture cuts found: {s['picture_cuts']['count']} (frames {s['picture_cuts']['frames']})", "",
              "## Evidence segments", ""]
    for seg in s["identity_segments"]:
        lines.append(f"- Segment {seg['segment_number']}: {seg['start_s']:.2f}-{seg['end_s']:.2f} s "
                     f"(frames {seg['start_frame']}-{seg['end_frame']}) {seg['state']}"
                     + (f" - {seg['reason']}" if seg["reason"] else ""))
    d, w = s["detection"], s["pose_status_when_target_locked"]
    lines += ["", "## Person detection", "",
              f"- {d['frames_processed']} frames, {d['person_detections']} person detections, at most "
              f"{d['max_persons_in_a_frame']} people in a frame, target LOCKED on {d['frames_with_target_locked']} frames "
              f"({d['frames_with_target_locked_percent']}%)"]
    if h["mode"].startswith("POSE"):
        lines += ["", f"## Pose on LOCKED frames ({w['frames']} frames)", ""]
        for state, n in w["counts"].items():
            lines.append(f"- {state}: {n} frames")
    lines += ["", "## Validation", ""]
    for c in h["validation"]["checks"]:
        lines.append(f"- [{'x' if c['passed'] else ' '}] {c['check']}")
    return "\n".join(lines) + "\n"
