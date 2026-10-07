"""A1 pipeline: video -> people -> the COACH-SELECTED target -> identity safety -> pose -> angles -> proxies.

Two decoding passes over the same upright frames:
  pass 1  every frame: YOLO person detections (+ a clothing-colour descriptor each), global camera shift,
          picture-cut evidence, sharpness
  (the coach's selection is resolved against pass 1's detections and followed: target_tracking)
  pass 2  only frames where the target is LOCKED: pose on the target's box
then angles and proxies are computed from those records.

Who the player is comes from the coach's tap (target_selection), never from "the biggest person". Pose runs only
on the box of a LOCKED target, so a pose can never belong to someone else, and nothing player-specific is computed
while the target is UNCERTAIN, LOST or RESELECTION_REQUIRED. Nothing is converted to metres or scored.
"""
import hashlib
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

from app.video_ai import geometry, identity, proxies, target_selection as ts, target_tracking as tt
from app.video_ai.config import DETECTION, IDENTITY, POSE, PROXY

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_YOLO = REPO_ROOT / "models" / "yolo26n.pt"
DEFAULT_POSE = REPO_ROOT / "models" / "pose_landmarker_lite.task"
SMALL = (135, 240)  # (width, height) of the downscaled portrait frame used for camera / picture-cut features


class AnalysisError(RuntimeError):
    pass


class NoTargetSelected(AnalysisError):
    """A player assessment cannot be analysed until the coach has tapped the player in the video."""


class SelectionNotOnAPerson(AnalysisError):
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
        "frame_count_container": int(stream.get("nb_frames") or len(pts_times)), "duration_s": float(info["format"]["duration"]),
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


def clothing_descriptor(frame_bgr, bbox_norm) -> list[float] | None:
    """A colour fingerprint of the person's torso (hue x saturation histogram of the middle of the box). It says
    what the clothing looks like, nothing about who the person is: it is not a face or body-biometric feature."""
    height, width = frame_bgr.shape[:2]
    x1, y1, x2, y2 = bbox_norm
    bw, bh = (x2 - x1), (y2 - y1)
    left, right = int((x1 + 0.2 * bw) * width), int((x1 + 0.8 * bw) * width)
    top, bottom = int((y1 + 0.2 * bh) * height), int((y1 + 0.65 * bh) * height)
    if right - left < 4 or bottom - top < 4:
        return None
    patch = cv2.resize(frame_bgr[top:bottom, left:right], (32, 32), interpolation=cv2.INTER_AREA)
    return [round(float(v), 4) for v in _hist(patch).flatten()]


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
        medium = cv2.resize(frame, (width // 2, height // 2), interpolation=cv2.INTER_AREA)
        sharpness = float(cv2.Laplacian(cv2.cvtColor(medium, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
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
                norm = [round(v, 5) for v in _bbox_norm(box, width, height)]
                detections.append({"track_id": track_id, "confidence": round(float(conf), 4), "bbox_norm": norm,
                                   "appearance": clothing_descriptor(frame, norm)})
        records.append({"frame_index": index, "detections": detections, "camera": camera,
                        "sharpness": round(sharpness, 2), "brightness": round(float(gray.mean()), 2)})
        index += 1
        if progress and index % 100 == 0:
            progress("detect", index)
    capture.release()
    return records


def picture_cuts(detect_records: list[dict]) -> list[bool]:
    """cuts[i] is True when frame i is the first frame of a new shot (consecutive frames look unrelated)."""
    limit = IDENTITY["scene_cut_hist_correlation"]
    return [r["camera"]["hist_correlation"] is not None and r["camera"]["hist_correlation"] < limit
            for r in detect_records]


def resolve_selections(detect_records: list[dict], selections: list[ts.TargetSelection]) -> list[tuple[int, int]]:
    """Each selection's tap, resolved against the people detected on its own frame. Refuses (raises) when the tap
    is not on anyone; never picks the nearest person."""
    resolved = []
    for selection in selections:
        if selection.tap_norm is None:
            raise SelectionNotOnAPerson("a selection needs the point the coach tapped")
        if not 0 <= selection.frame_index < len(detect_records):
            raise SelectionNotOnAPerson(f"frame {selection.frame_index} is not in the video")
        index, message = ts.resolve_tap(detect_records[selection.frame_index]["detections"], selection.tap_norm)
        if index is None:
            raise SelectionNotOnAPerson(message)
        resolved.append((selection.frame_index, index))
    return resolved


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


def pose_pass(video: Path, info: dict, target_boxes: list[list[float] | None], pose_model: Path,
              progress=None) -> list[dict]:
    """Pose on the target's box, and only where `target_boxes[i]` is given (frames where the target is LOCKED)."""
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
    out, index = [], 0
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            box = target_boxes[index]
            entry = {"landmarks": None, "pose_failure": None, "crop_px": None, "rejected_reason": None}
            if box is None:
                entry["pose_failure"] = "TARGET_NOT_LOCKED"
            else:
                x1, y1, x2, y2 = box
                margin_x, margin_y = (x2 - x1) * POSE["crop_margin"], (y2 - y1) * POSE["crop_margin"]
                px1, py1 = int(max(0, (x1 - margin_x) * width)), int(max(0, (y1 - margin_y) * height))
                px2, py2 = int(min(width, (x2 + margin_x) * width)), int(min(height, (y2 + margin_y) * height))
                if px2 - px1 < 8 or py2 - py1 < 8:
                    entry["pose_failure"] = "TARGET_BOX_TOO_SMALL"
                else:
                    crop = np.ascontiguousarray(frame[py1:py2, px1:px2])
                    entry["crop_px"] = [px1, py1, px2 - px1, py2 - py1]
                    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    result = landmarker.detect(image)
                    if result.pose_landmarks:
                        entry["landmarks"] = _landmark_dict(result, entry["crop_px"], width, height, box)
                        entry["rejected_reason"] = geometry.apply_plausibility(entry["landmarks"], box, width / height)
                    else:
                        entry["pose_failure"] = "NO_POSE_IN_TARGET_BOX"
            out.append(entry)
            index += 1
            if progress and index % 100 == 0:
                progress("pose", index)
    capture.release()
    return out


def no_pose(count: int, why: str) -> list[dict]:
    return [{"landmarks": None, "pose_failure": why, "crop_px": None, "rejected_reason": None} for _ in range(count)]


def _touches_edge(bbox_norm, tolerance=0.005) -> bool:
    x1, y1, x2, y2 = bbox_norm
    return x1 <= tolerance or y1 <= tolerance or x2 >= 1 - tolerance or y2 >= 1 - tolerance


def _overlap(detections, target) -> bool:
    return any(d is not target and tt.iou(d["bbox_norm"], target["bbox_norm"]) >= IDENTITY["overlap_iou"]
               for d in detections)


def assemble(info: dict, detect_records: list[dict], pose_records: list[dict], tracking: list[dict],
             cuts: list[bool], identity_only: bool = False) -> tuple[list[dict], dict]:
    width, height = info["display_width"], info["display_height"]
    aspect = width / height
    times = info["presentation_timestamps_s"]
    count = len(detect_records)
    if len(times) != count:
        raise AnalysisError(f"decoded {count} frames but the container lists {len(times)} timestamps")

    frames = []
    for i in range(count):
        det_rec, pose_rec, track = detect_records[i], pose_records[i], tracking[i]
        target = track["target"]
        state = track["state"]
        identity_ok = state == tt.LOCKED
        landmarks = pose_rec["landmarks"] if identity_ok else None
        quality = geometry.pose_quality(
            landmarks,
            overlap=bool(target) and _overlap(det_rec["detections"], target),
            touches_edge=bool(target) and _touches_edge(target["bbox_norm"]),
            rejected_reason=pose_rec["rejected_reason"] if identity_ok else None)
        if landmarks is None:
            quality["reason"] = pose_rec["pose_failure"] if identity_ok and pose_rec["pose_failure"] else f"TARGET_{state}"
        angles = geometry.compute_angles(landmarks, identity_ok, f"IDENTITY_{state}" if not identity_ok else None)
        camera = det_rec["camera"]
        shift = camera["global_shift_norm"]
        moving = bool(shift) and (shift[0] ** 2 + shift[1] ** 2) ** 0.5 > PROXY["camera_motion_flag_norm"]
        frames.append({
            "frame_index": i, "timestamp_s": round(times[i], 6),
            "persons": [{"track_id": d["track_id"], "confidence": d["confidence"], "bbox_norm": d["bbox_norm"],
                         "is_target": bool(target) and d is target} for d in det_rec["detections"]],
            "target": None if target is None else {
                "bbox_norm": target["bbox_norm"],
                "bbox_px": [round(target["bbox_norm"][0] * width, 1), round(target["bbox_norm"][1] * height, 1),
                            round(target["bbox_norm"][2] * width, 1), round(target["bbox_norm"][3] * height, 1)],
                "detection_confidence": target["confidence"],
                "tracking_confidence": tt.tracking_confidence(target["confidence"], state)},
            "identity": {"state": state, "reason": track["reason"], "segment": track["segment"],
                         "epoch": track["epoch"], "direction": track.get("direction"), "picture_cut": cuts[i],
                         "appearance_similarity": None if track["appearance_similarity"] is None
                         else round(track["appearance_similarity"], 4),
                         "last_known_box_norm": track["last_known_box_norm"] if not identity_ok else None},
            "pose": {"quality": quality, "crop_px": pose_rec["crop_px"] if identity_ok else None, "landmarks": landmarks},
            "angles": angles, "camera": {**camera, "camera_moving": moving},
            "_center": None if (target is None or identity_only) else [(target["bbox_norm"][0] + target["bbox_norm"][2]) / 2,
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
    return frames, {"windows": windows}


def _summaries(frames: list[dict], windows: list[dict], info: dict, tracks: dict):
    total = len(frames)
    times = [f["timestamp_s"] for f in frames]
    pose_counts = {s: 0 for s in geometry.POSE_STATES}
    state_counts: dict[str, int] = {}
    for f in frames:
        pose_counts[f["pose"]["quality"]["status"]] += 1
        state_counts[f["identity"]["state"]] = state_counts.get(f["identity"]["state"], 0) + 1
    pose_percent = {k: round(100.0 * v / total, 2) for k, v in pose_counts.items()}
    segments = tt.evidence_segments([{"state": f["identity"]["state"], "epoch": f["identity"]["epoch"],
                                      "reason": f["identity"]["reason"]} for f in frames], times)
    cut_frames = [f["frame_index"] for f in frames if f["identity"]["picture_cut"]]
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
    locked = [f for f in frames if f["identity"]["state"] == tt.LOCKED]
    when_target = {s: 0 for s in geometry.POSE_STATES}
    for f in locked:
        when_target[f["pose"]["quality"]["status"]] += 1
    person_counts = [len(f["persons"]) for f in frames]
    detection = {"frames_processed": total, "frames_with_a_person": sum(1 for n in person_counts if n),
                 "person_detections": sum(person_counts), "max_persons_in_a_frame": max(person_counts, default=0),
                 "detector_track_ids_seen": len(tracks), "frames_with_target_locked": len(locked),
                 "frames_with_target_locked_percent": round(100.0 * len(locked) / total, 2)}
    motion = {"speed_frames_measured": sum(1 for f in frames
                                           if f["motion_proxy"]["speed_proxy_frame_heights_per_s"]["state"] == "MEASURED"),
              "camera_moving_frames": sum(1 for f in frames if f["camera"]["camera_moving"])}
    motion["camera_moving_percent"] = round(100.0 * motion["camera_moving_frames"] / total, 2)
    motion["windows_total"] = len(windows)
    motion["windows_with_cadence_proxy"] = sum(
        1 for w in windows if "MEASURED" in (w["left_knee_dominant_frequency_hz"]["state"],
                                             w["right_knee_dominant_frequency_hz"]["state"]))
    return {"detection": detection,
            "pose_status_when_target_locked": {
                "frames": len(locked), "counts": when_target,
                "percent": {k: round(100.0 * v / len(locked), 2) if locked else None for k, v in when_target.items()}},
            "frames": total, "pose_status_counts": pose_counts, "pose_status_percent": pose_percent,
            "identity_state_counts": state_counts,
            "identity_state_percent": {k: round(100.0 * v / total, 2) for k, v in state_counts.items()},
            "identity_segments": segments, "picture_cuts": {"frames": cut_frames, "count": len(cut_frames)},
            "angle_measurability": measurable, "motion": motion}
