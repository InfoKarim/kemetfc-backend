"""A1 pipeline: video -> person tracking -> target identity -> pose on the target -> angles -> proxies -> artifact.

Two decoding passes over the same upright frames:
  pass 1  every frame: YOLO person detection + ByteTrack ids, global camera shift, colour histogram
  (select the target track, offline)
  pass 2  every frame: pose on the target's box only, plus an appearance check of the target box
then identity continuity, angles and proxies are computed from those records.

The pose model only ever sees the target's box, so a pose can never belong to a different person in the frame.
Nothing is converted to metres or scored.
"""
import hashlib
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from app.video_ai import geometry, identity, proxies
from app.video_ai.config import (DETECTION, IDENTITY, PIPELINE_VERSION, POSE, PROXY, SCHEMA_VERSION)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YOLO = REPO_ROOT / "models" / "yolo26n.pt"
DEFAULT_POSE = REPO_ROOT / "models" / "pose_landmarker_lite.task"
SMALL = (135, 240)  # (width, height) of the downscaled portrait frame used for camera / appearance features


class AnalysisError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_video(path: Path) -> dict:
    """Container facts and the presentation timestamp of every frame, from ffprobe (the time base of record)."""
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format", "-of", "json",
         str(path)], capture_output=True, text=True, check=True).stdout)
    stream = info["streams"][0]
    rotation = 0
    for item in stream.get("side_data_list", []):
        if "rotation" in item:
            rotation = int(item["rotation"])
    stored_w, stored_h = int(stream["width"]), int(stream["height"])
    swapped = abs(rotation) % 180 == 90
    pts = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=best_effort_timestamp_time",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.split()
    pts_times = [float(value.strip(",")) for value in pts if value.strip(",")]
    num, den = (int(v) for v in stream["avg_frame_rate"].split("/"))
    return {
        "codec": stream["codec_name"], "stored_width": stored_w, "stored_height": stored_h,
        "rotation_degrees": rotation,
        "display_width": stored_h if swapped else stored_w, "display_height": stored_w if swapped else stored_h,
        "declared_frame_rate": stream.get("r_frame_rate"), "average_fps": num / den,
        "frame_count_container": int(stream["nb_frames"]), "duration_s": float(info["format"]["duration"]),
        "presentation_timestamps_s": pts_times,
    }


def _bbox_norm(xyxy, width, height):
    x1, y1, x2, y2 = xyxy
    return [min(max(x1 / width, 0.0), 1.0), min(max(y1 / height, 0.0), 1.0),
            min(max(x2 / width, 0.0), 1.0), min(max(y2 / height, 0.0), 1.0)]


def _hist(image_bgr) -> np.ndarray:
    hist = cv2.calcHist([cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist


def _load_models(yolo_path: Path, pose_path: Path):
    from app.full_match_analyzer import _load_yolo  # the repository's existing YOLO loader
    for needed in (yolo_path, pose_path):
        if not Path(needed).is_file():
            raise AnalysisError(f"model file missing: {needed}")
    return _load_yolo(yolo_path)


def detect_pass(video: Path, info: dict, model, progress=None) -> list[dict]:
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise AnalysisError("could not open the video")
    width, height = info["display_width"], info["display_height"]
    names = {int(k): str(v).lower() for k, v in model.names.items()}
    person_ids = [k for k, v in names.items() if v in ("person", "player")]
    if not person_ids:
        raise AnalysisError("the detector has no person/player class")
    window = cv2.createHanningWindow(SMALL, cv2.CV_32F)
    records, previous_gray, previous_hist, index = [], None, None, 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if frame.shape[1] != width or frame.shape[0] != height:
            raise AnalysisError(f"decoded frame {frame.shape} is not the upright display size {width}x{height}")
        small = cv2.resize(frame, SMALL, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hist = _hist(small)
        camera = {"global_shift_norm": None, "shift_response": None, "hist_correlation": None}
        if previous_gray is not None:
            (dx, dy), response = cv2.phaseCorrelate(previous_gray, gray, window)
            camera = {"global_shift_norm": [round(dx / SMALL[0], 5), round(dy / SMALL[1], 5)],
                      "shift_response": round(float(response), 4),
                      "hist_correlation": round(float(cv2.compareHist(previous_hist, hist, cv2.HISTCMP_CORREL)), 4)}
        previous_gray, previous_hist = gray, hist
        result = model.track(frame, persist=True, tracker=DETECTION["tracker"], classes=person_ids,
                             conf=DETECTION["confidence_threshold"], iou=DETECTION["iou_threshold"],
                             imgsz=DETECTION["image_size"], device=DETECTION["device"], verbose=False)[0]
        detections = []
        boxes = result.boxes
        if boxes is not None and len(boxes):
            xyxy = boxes.xyxy.cpu().tolist()
            confs = boxes.conf.cpu().tolist()
            ids = boxes.id.int().cpu().tolist() if boxes.id is not None else [None] * len(xyxy)
            for box, conf, track_id in zip(xyxy, confs, ids):
                detections.append({"track_id": track_id, "confidence": round(float(conf), 4),
                                   "bbox_norm": [round(v, 5) for v in _bbox_norm(box, width, height)]})
        records.append({"frame_index": index, "detections": detections, "camera": camera})
        index += 1
        if progress and index % 100 == 0:
            progress("detect", index)
    capture.release()
    return records


def _landmark_dict(result, crop_box_px, width, height, bbox_norm):
    """33 landmarks in full-frame pixels/normalized coordinates, each with a usable flag."""
    x0, y0, crop_w, crop_h = crop_box_px
    marks = {}
    for i, mark in enumerate(result.pose_landmarks[0]):
        x_px = x0 + mark.x * crop_w
        y_px = y0 + mark.y * crop_h
        xn, yn = x_px / width, y_px / height
        visibility = None if mark.visibility is None else float(mark.visibility)
        presence = None if mark.presence is None else float(mark.presence)
        usable, why = geometry.landmark_usable(visibility, presence, xn, yn, bbox_norm)
        marks[geometry.LANDMARK_NAMES[i]] = {"x_px": float(x_px), "y_px": float(y_px), "x_norm": float(xn),
                                             "y_norm": float(yn), "visibility": visibility or 0.0,
                                             "presence": presence or 0.0, "usable": usable, "unusable_reason": why}
    return marks


def pose_pass(video: Path, info: dict, detect_records: list[dict], target_id: int | None, pose_model: Path,
              progress=None) -> list[dict]:
    import mediapipe as mp
    width, height = info["display_width"], info["display_height"]
    vision = mp.tasks.vision
    options = vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(pose_model),
                                          delegate=mp.tasks.BaseOptions.Delegate.CPU),
        running_mode=vision.RunningMode.IMAGE, num_poses=1,
        min_pose_detection_confidence=POSE["min_pose_detection_confidence"],
        min_pose_presence_confidence=POSE["min_pose_presence_confidence"], output_segmentation_masks=False)
    capture = cv2.VideoCapture(str(video))
    out, previous_crop_hist, index = [], None, 0
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            target = next((d for d in detect_records[index]["detections"]
                           if target_id is not None and d["track_id"] == target_id), None)
            entry = {"landmarks": None, "pose_failure": None, "crop_px": None, "appearance_correlation": None,
                     "rejected_reason": None}
            if target is None:
                entry["pose_failure"] = "TARGET_NOT_DETECTED"
                previous_crop_hist = None
            else:
                x1, y1, x2, y2 = target["bbox_norm"]
                margin_x, margin_y = (x2 - x1) * POSE["crop_margin"], (y2 - y1) * POSE["crop_margin"]
                px1 = int(max(0, (x1 - margin_x) * width))
                py1 = int(max(0, (y1 - margin_y) * height))
                px2 = int(min(width, (x2 + margin_x) * width))
                py2 = int(min(height, (y2 + margin_y) * height))
                if px2 - px1 < 8 or py2 - py1 < 8:
                    entry["pose_failure"] = "TARGET_BOX_TOO_SMALL"
                else:
                    crop = np.ascontiguousarray(frame[py1:py2, px1:px2])
                    entry["crop_px"] = [px1, py1, px2 - px1, py2 - py1]
                    crop_hist = _hist(cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA))
                    if previous_crop_hist is not None:
                        entry["appearance_correlation"] = round(
                            float(cv2.compareHist(previous_crop_hist, crop_hist, cv2.HISTCMP_CORREL)), 4)
                    previous_crop_hist = crop_hist
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    result = landmarker.detect(image)
                    if result.pose_landmarks:
                        entry["landmarks"] = _landmark_dict(result, entry["crop_px"], width, height,
                                                            target["bbox_norm"])
                        entry["rejected_reason"] = geometry.apply_plausibility(entry["landmarks"],
                                                                               target["bbox_norm"], width / height)
                    else:
                        entry["pose_failure"] = "NO_POSE_IN_TARGET_BOX"
            out.append(entry)
            index += 1
            if progress and index % 100 == 0:
                progress("pose", index)
    capture.release()
    return out


def _touches_edge(bbox_norm, tolerance=0.005) -> bool:
    x1, y1, x2, y2 = bbox_norm
    return x1 <= tolerance or y1 <= tolerance or x2 >= 1 - tolerance or y2 >= 1 - tolerance


def _overlap(detections, target) -> bool:
    return any(d is not target and d.get("track_id") != target.get("track_id")
               and identity._iou(d["bbox_norm"], target["bbox_norm"]) >= IDENTITY["overlap_iou"]
               for d in detections)


def assemble(info: dict, detect_records: list[dict], pose_records: list[dict], target_id: int | None,
             tracks: dict, selection_method: str) -> tuple[list[dict], dict]:
    width, height = info["display_width"], info["display_height"]
    aspect = width / height
    times = info["presentation_timestamps_s"]
    count = len(detect_records)
    if len(times) != count:
        raise AnalysisError(f"decoded {count} frames but the container lists {len(times)} timestamps")

    cuts = []
    for i in range(count):
        corr_global = detect_records[i]["camera"]["hist_correlation"]
        corr_target = pose_records[i]["appearance_correlation"]
        cuts.append((corr_global is not None and corr_global < IDENTITY["scene_cut_hist_correlation"]) or
                    (corr_target is not None and corr_target < IDENTITY["appearance_break_hist_correlation"]))
    continuity = identity.assess_identity([r["detections"] for r in detect_records], target_id, cuts, aspect)

    frames = []
    for i in range(count):
        det_rec, pose_rec, cont = detect_records[i], pose_records[i], continuity[i]
        target = cont["target"]
        state = cont["state"]
        identity_ok = state == "CONFIRMED"
        landmarks = pose_rec["landmarks"]
        quality = geometry.pose_quality(
            landmarks,
            overlap=bool(target) and _overlap(det_rec["detections"], target),
            touches_edge=bool(target) and _touches_edge(target["bbox_norm"]),
            rejected_reason=pose_rec["rejected_reason"])
        if landmarks is None and pose_rec["pose_failure"]:
            quality["reason"] = pose_rec["pose_failure"]
        angles = geometry.compute_angles(landmarks, identity_ok, f"IDENTITY_{state}" if not identity_ok else None)
        camera = det_rec["camera"]
        shift = camera["global_shift_norm"]
        moving = bool(shift) and (shift[0] ** 2 + shift[1] ** 2) ** 0.5 > PROXY["camera_motion_flag_norm"]
        frames.append({
            "frame_index": i, "timestamp_s": round(times[i], 6),
            "persons": [{"track_id": d["track_id"], "confidence": d["confidence"], "bbox_norm": d["bbox_norm"],
                         "is_target": bool(target) and d is target} for d in det_rec["detections"]],
            "target": None if target is None else {
                "track_id": target["track_id"], "bbox_norm": target["bbox_norm"],
                "bbox_px": [round(target["bbox_norm"][0] * width, 1), round(target["bbox_norm"][1] * height, 1),
                            round(target["bbox_norm"][2] * width, 1), round(target["bbox_norm"][3] * height, 1)],
                "detection_confidence": target["confidence"],
                "tracking_confidence": identity.tracking_confidence(target["confidence"], state)},
            "identity": {"state": state, "reason": cont["reason"], "segment": cont["segment"],
                         "scene_or_appearance_break": cuts[i]},
            "pose": {"quality": quality, "crop_px": pose_rec["crop_px"], "landmarks": landmarks},
            "angles": angles, "camera": {**camera, "camera_moving": moving},
            "_center": None if target is None else [(target["bbox_norm"][0] + target["bbox_norm"][2]) / 2,
                                                    (target["bbox_norm"][1] + target["bbox_norm"][3]) / 2],
        })

    series = [{"t": f["timestamp_s"], "state": f["identity"]["state"], "segment": f["identity"]["segment"],
               "center": f["_center"], "camera_moving": f["camera"]["camera_moving"],
               "angles": {n: a["value"] for n, a in f["angles"].items()}} for f in frames]
    per_frame = proxies.per_frame_proxies(series, aspect)
    for f, p in zip(frames, per_frame):
        f["motion_proxy"] = p
        del f["_center"]
    windows = proxies.window_proxies(series, info["average_fps"])
    return frames, {"windows": windows, "continuity": continuity}


def _summaries(frames: list[dict], windows: list[dict], info: dict, tracks: dict, target_id, selection_method: str):
    total = len(frames)
    pose_counts = {s: 0 for s in geometry.POSE_STATES}
    state_counts: dict[str, int] = {}
    for f in frames:
        pose_counts[f["pose"]["quality"]["status"]] += 1
        state_counts[f["identity"]["state"]] = state_counts.get(f["identity"]["state"], 0) + 1
    pose_percent = {k: round(100.0 * v / total, 2) for k, v in pose_counts.items()}
    uncertain = identity.runs([(f["frame_index"], None if f["identity"]["state"] == "CONFIRMED"
                                else f"{f['identity']['state']}:{f['identity']['reason']}") for f in frames])
    for run in uncertain:
        run["start_s"] = frames[run["start_frame"]]["timestamp_s"]
        run["end_s"] = frames[run["end_frame"]]["timestamp_s"]
        run["state"], run["reason"] = run.pop("label").split(":", 1)
    measurable = {}
    for name in geometry.ANGLE_DEFINITIONS:
        measured = [f["angles"][name]["value"] for f in frames if f["angles"][name]["state"] == "MEASURED"]
        reasons: dict[str, int] = {}
        for f in frames:
            entry = f["angles"][name]
            if entry["state"] != "MEASURED":
                key = (entry["reason"] or "UNKNOWN").split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
        measurable[name] = {"frames_measured": len(measured), "percent_measured": round(100.0 * len(measured) / total, 2),
                            "min": min(measured) if measured else None, "max": max(measured) if measured else None,
                            "mean": round(float(np.mean(measured)), 3) if measured else None,
                            "not_measurable_reasons": reasons}
    motion = {"speed_frames_measured": sum(1 for f in frames
                                           if f["motion_proxy"]["speed_proxy_frame_heights_per_s"]["state"] == "MEASURED"),
              "camera_moving_frames": sum(1 for f in frames if f["camera"]["camera_moving"])}
    motion["camera_moving_percent"] = round(100.0 * motion["camera_moving_frames"] / total, 2)
    motion["windows_total"] = len(windows)
    motion["windows_with_cadence_proxy"] = sum(
        1 for w in windows if "MEASURED" in (w["left_knee_dominant_frequency_hz"]["state"],
                                             w["right_knee_dominant_frequency_hz"]["state"]))
    detected = [f for f in frames if f["target"] is not None]
    when_target = {s: 0 for s in geometry.POSE_STATES}
    for f in detected:
        when_target[f["pose"]["quality"]["status"]] += 1
    person_counts = [len(f["persons"]) for f in frames]
    detection = {"frames_processed": total, "frames_with_a_person": sum(1 for n in person_counts if n),
                 "person_detections": sum(person_counts), "max_persons_in_a_frame": max(person_counts, default=0),
                 "distinct_tracks": len(tracks), "frames_with_target_detected": len(detected),
                 "frames_with_target_detected_percent": round(100.0 * len(detected) / total, 2)}
    return {"detection": detection,
            "pose_status_when_target_detected": {
                "frames": len(detected), "counts": when_target,
                "percent": {k: round(100.0 * v / len(detected), 2) if detected else None for k, v in when_target.items()}},
            "frames": total, "pose_status_counts": pose_counts, "pose_status_percent": pose_percent,
            "identity_state_counts": state_counts, "uncertain_intervals": uncertain,
            "angle_measurability": measurable, "motion": motion}
