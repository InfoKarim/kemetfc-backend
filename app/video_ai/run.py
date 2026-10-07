"""Run A1 end to end and write the evidence artifact (JSON header + per-frame JSONL + short human summary)."""
import json
import platform
import time
from pathlib import Path

import cv2
import mediapipe
import torch
import ultralytics

from app.video_ai import geometry, identity, pipeline, proxies, validate
from app.video_ai.config import (ANGLE_DEFINITION_VERSION, DETECTION, IDENTITY, PIPELINE_VERSION, POSE, PROXY,
                                 PROXY_DEFINITION_VERSION, SCHEMA_VERSION)

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
        runs = identity.runs(labelled)
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


def run_a1(video: Path, out_dir: Path, recording_id: str, player_id: str, yolo_model: Path = pipeline.DEFAULT_YOLO,
           pose_model: Path = pipeline.DEFAULT_POSE, progress=print) -> dict:
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    sha_before = pipeline.sha256_file(video)
    info = pipeline.probe_video(video)
    model = pipeline._load_models(yolo_model, pose_model)

    detect_records = pipeline.detect_pass(video, info, model, lambda p, n: progress(f"{p}: {n} frames"))
    if len(detect_records) != len(info["presentation_timestamps_s"]):
        raise pipeline.AnalysisError("frame count decoded differs from the container timestamps")
    target_id, tracks, selection = identity.select_target([r["detections"] for r in detect_records],
                                                          info["average_fps"])
    pose_records = pipeline.pose_pass(video, info, detect_records, target_id, pose_model,
                                      lambda p, n: progress(f"{p}: {n} frames"))
    frames, extra = pipeline.assemble(info, detect_records, pose_records, target_id, tracks, selection)
    windows = extra["windows"]
    summaries = pipeline._summaries(frames, windows, info, tracks, target_id, selection)
    checks = validate.validate(frames, info["presentation_timestamps_s"], target_id, windows)

    frames_path = out_dir / "a1_frames.jsonl"
    with open(frames_path, "w") as handle:
        for f in frames:
            handle.write(json.dumps(f, separators=(",", ":"), sort_keys=True) + "\n")
    sha_after = pipeline.sha256_file(video)

    header = {
        "schema_version": SCHEMA_VERSION, "pipeline_version": PIPELINE_VERSION, "phase": "A1_POSE_BODY_MOVEMENT",
        "evidence_not_score": True, "labels": LABELS,
        "recording_id": recording_id, "player_id": player_id,
        "video": {"path": str(video), "sha256": sha_before, "sha256_after_analysis": sha_after,
                  "unchanged": sha_before == sha_after, **{k: v for k, v in info.items()
                                                           if k != "presentation_timestamps_s"},
                  "timestamp_source": "ffprobe best_effort_timestamp_time per decoded frame, checked 1:1 against "
                                      "the decoded frame count", "frame_coordinates": "upright display frame; "
                  "x,y in pixels of display_width x display_height, normalized 0..1 with origin top-left"},
        "models": {
            "person_detector_tracker": {"name": "Ultralytics YOLO + ByteTrack", "weights": str(yolo_model),
                                        "weights_sha256": pipeline.sha256_file(yolo_model),
                                        "ultralytics": ultralytics.__version__, "torch": torch.__version__,
                                        "classes_used": ["person"], "settings": DETECTION},
            "pose": {"name": "MediaPipe Pose Landmarker (lite, 33 landmarks)", "weights": str(pose_model),
                     "weights_sha256": pipeline.sha256_file(pose_model), "mediapipe": mediapipe.__version__,
                     "mode": "IMAGE on the target's box only", "settings": POSE},
            "runtime": {"opencv": cv2.__version__, "python": platform.python_version(), "platform": platform.platform()},
        },
        "frame_sampling": {"method": "every decoded frame (stride 1)", "frames_processed": len(frames),
                           "reason": "no frame is skipped so cadence/stride proxies keep the full time resolution; "
                                     "the cost is acceptable for a 32 s clip"},
        "target_selection": {"method": selection, "target_track_id": target_id,
                             "identity_verified_against_named_player": False,
                             "note": "no player-selection hint was available; the target is the dominant tracked "
                                     "person, not a verified Test Player A"},
        "thresholds": {"identity": IDENTITY, "pose": POSE, "proxy": PROXY},
        "definitions": {"angle_definition_version": ANGLE_DEFINITION_VERSION,
                        "proxy_definition_version": PROXY_DEFINITION_VERSION,
                        "angles": geometry.ANGLE_DESCRIPTIONS, "core_joints": geometry.CORE_JOINTS,
                        "landmark_order": geometry.LANDMARK_NAMES,
                        "pose_states": {"GOOD": f">= {POSE['good_min_usable_core']} of 12 core joints usable and mean "
                                                f"core visibility >= {POSE['good_min_mean_visibility']}",
                                        "PARTIAL": f">= {POSE['partial_min_usable_core']} core joints usable",
                                        "LOW_CONFIDENCE": "pose found but fewer usable core joints than PARTIAL",
                                        "OCCLUDED": f">= {POSE['occluded_min_hidden_core']} core joints hidden while "
                                                    "another person overlaps the target or the box touches the edge",
                                        "NOT_DETECTED": "no pose for the target"},
                        "landmark_usable": "visibility and presence >= 0.5, inside the frame, inside the target box "
                                           "grown by 10%"},
        "per_frame_file": {"path": frames_path.name, "format": "JSON Lines, one object per frame",
                           "sha256": pipeline.sha256_file(frames_path)},
        "tracks": sorted(tracks.values(), key=lambda t: t["track_id"]),
        "summary": summaries, "windows": windows,
        "not_measurable_intervals": _not_measurable_runs(frames),
        "validation": {"checks": checks, "all_passed": all(c["passed"] for c in checks)},
        "run": {"seconds": round(time.time() - started, 1)},
    }
    (out_dir / "a1_analysis.json").write_text(json.dumps(header, indent=1, sort_keys=True))
    (out_dir / "a1_summary.md").write_text(render_summary(header))
    return header


def render_summary(h: dict) -> str:
    s, v = h["summary"], h["video"]
    lines = [f"# A1 pose and body-movement evidence - recording {h['recording_id']}",
             "", "Evidence only. No score, no rating, no physical speed.", "",
             f"- player id: {h['player_id']} (not verified against the footage)",
             f"- video: {v['display_width']}x{v['display_height']} upright ({v['stored_width']}x{v['stored_height']} stored, "
             f"rotation {v['rotation_degrees']}), {v['frame_count_container']} frames, {v['duration_s']:.2f} s, "
             f"~{v['average_fps']:.2f} fps, SHA-256 `{v['sha256'][:16]}...` unchanged: {v['unchanged']}",
             f"- target: track {h['target_selection']['target_track_id']} ({h['target_selection']['method']}), "
             f"identity not verified against a named player",
             "", "## Pose quality (all frames)", ""]
    for state, pct in s["pose_status_percent"].items():
        lines.append(f"- {state}: {s['pose_status_counts'][state]} frames ({pct}%)")
    d, w = s["detection"], s["pose_status_when_target_detected"]
    lines += ["", f"While the target box exists ({w['frames']} frames, {d['frames_with_target_detected_percent']}% of the video):", ""]
    for state, n in w["counts"].items():
        lines.append(f"- {state}: {n} frames ({w['percent'][state]}%)")
    lines += ["", "## Person detection", "",
              f"- {d['frames_processed']} frames processed, {d['person_detections']} person detections, "
              f"{d['frames_with_a_person']} frames with at least one person, {d['distinct_tracks']} distinct tracks, "
              f"at most {d['max_persons_in_a_frame']} people in a frame"]
    lines += ["", "## Identity", ""]
    for state, n in s["identity_state_counts"].items():
        lines.append(f"- {state}: {n} frames")
    lines.append(f"- uncertain or lost intervals: {len(s['uncertain_intervals'])}")
    lines += ["", "## Angles (image plane, 2D)", ""]
    for name, m in s["angle_measurability"].items():
        rng = "" if m["mean"] is None else f", mean {m['mean']}, range {m['min']}..{m['max']}"
        lines.append(f"- {name}: {m['frames_measured']} frames ({m['percent_measured']}%){rng}")
    mo = s["motion"]
    lines += ["", "## Movement proxies (VIDEO_MOTION_PROXY)", "",
              f"- speed proxy measured on {mo['speed_frames_measured']} frames (image units, camera not compensated)",
              f"- camera moving on {mo['camera_moving_percent']}% of frames",
              f"- windows with a cadence proxy: {mo['windows_with_cadence_proxy']} of {mo['windows_total']}",
              "", "## Validation", ""]
    for c in h["validation"]["checks"]:
        lines.append(f"- [{'x' if c['passed'] else ' '}] {c['check']}")
    return "\n".join(lines) + "\n"
