"""Target-player selection and identity continuity.

There is no re-identification model in A1: identity is the tracker's id plus explicit guards. The target id is
fixed once. If the tracker loses it, the frames are marked LOST - a new id appearing nearby is NEVER adopted. A
cut in the picture, an implausible jump, or another person overlapping the target puts the frame in an uncertain
state, and measurements that would span such frames are NOT_MEASURABLE.
"""
from app.video_ai.config import IDENTITY


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def track_summaries(frames: list[list[dict]], fps: float) -> dict[int, dict]:
    """frames[i] = detections of frame i: {track_id|None, confidence, bbox_norm (x1,y1,x2,y2 in 0..1)}."""
    tracks: dict[int, dict] = {}
    for index, detections in enumerate(frames):
        for det in detections:
            track_id = det.get("track_id")
            if track_id is None:
                continue
            entry = tracks.setdefault(track_id, {"track_id": track_id, "first_frame": index, "last_frame": index,
                                                 "frames": 0, "confidence_sum": 0.0, "weight": 0.0})
            entry["last_frame"] = index
            entry["frames"] += 1
            entry["confidence_sum"] += det["confidence"]
            box = det["bbox_norm"]
            entry["weight"] += det["confidence"] * (box[2] - box[0]) * (box[3] - box[1])
    for entry in tracks.values():
        entry["seconds_present"] = round(entry["frames"] / fps, 3)
        entry["mean_confidence"] = round(entry["confidence_sum"] / entry["frames"], 4)
        entry["weight"] = round(entry["weight"], 6)
        del entry["confidence_sum"]
    return tracks


def select_target(frames: list[list[dict]], fps: float) -> tuple[int | None, dict[int, dict], str]:
    """Offline rule used when no player-selection hint is available: the track with the largest total
    confidence x on-screen area, among tracks present for at least min_target_presence_seconds. The first id
    wins a tie. This is NOT an identity check against a named player."""
    tracks = track_summaries(frames, fps)
    eligible = [t for t in tracks.values() if t["seconds_present"] >= IDENTITY["min_target_presence_seconds"]]
    if not eligible:
        return None, tracks, "no tracked person was present long enough"
    best = max(eligible, key=lambda t: (t["weight"], -t["track_id"]))
    return best["track_id"], tracks, "dominant_track_offline"


def assess_identity(frames: list[list[dict]], target_id: int | None, scene_cuts: list[bool],
                    aspect_ratio: float) -> list[dict]:
    """Per frame: continuity state, reason, the target detection (or None) and its identity segment.
    aspect_ratio = frame width / frame height, so a jump can be measured in frame heights."""
    guard = IDENTITY
    out: list[dict] = []
    previous_det = None
    gap_length = 0
    probation = 0
    probation_reason = None
    segment = 0
    previous_state = "LOST"
    ever_seen = False
    for index, detections in enumerate(frames):
        det = next((d for d in detections if target_id is not None and d.get("track_id") == target_id), None)
        cut = bool(scene_cuts[index]) if index < len(scene_cuts) else False
        record = {"frame_index": index, "state": "LOST", "reason": "TARGET_NOT_DETECTED", "target": None,
                  "segment": None}
        if det is None:
            gap_length += 1
            previous_det = None
            probation = 0
            previous_state = "LOST"
            out.append(record)
            continue

        record["target"] = det
        if not ever_seen:
            probation, probation_reason = guard["reacquire_probation_frames"], "TARGET_FIRST_SEEN"
        elif previous_state == "LOST" and gap_length >= guard["reacquire_gap_frames"]:
            probation, probation_reason = guard["reacquire_probation_frames"], "REACQUIRED_AFTER_GAP"
        ever_seen = True
        gap_length = 0
        if cut:
            probation, probation_reason = guard["reacquire_probation_frames"], "AFTER_SCENE_CUT"
        elif previous_det is not None:
            (cx, cy), (px, py) = _center(det["bbox_norm"]), _center(previous_det["bbox_norm"])
            jump = (((cx - px) * aspect_ratio) ** 2 + (cy - py) ** 2) ** 0.5
            if jump > guard["max_center_jump_frame_heights"]:
                probation, probation_reason = guard["reacquire_probation_frames"], "IMPLAUSIBLE_JUMP"
        previous_det = det

        overlapping = [d for d in detections if d is not det and d.get("track_id") != target_id
                       and _iou(d["bbox_norm"], det["bbox_norm"]) >= guard["overlap_iou"]]
        if probation > 0:
            record["state"], record["reason"] = "REACQUIRED_UNVERIFIED", probation_reason
            probation -= 1
        elif overlapping:
            record["state"], record["reason"] = "OVERLAP_AMBIGUOUS", "ANOTHER_PERSON_OVERLAPS_TARGET"
        else:
            record["state"], record["reason"] = "CONFIRMED", None
        if record["state"] == "CONFIRMED":
            if previous_state != "CONFIRMED":
                segment += 1
            record["segment"] = segment
        previous_state = record["state"]
        out.append(record)
    return out


def tracking_confidence(detection_confidence: float | None, state: str) -> float:
    """Heuristic, NOT a calibrated probability: the detector's confidence scaled by how trustworthy the identity
    is in this frame."""
    if detection_confidence is None:
        return 0.0
    return round(detection_confidence * IDENTITY["continuity_factor"][state], 4)


def runs(frame_indices_and_labels: list[tuple[int, str | None]]) -> list[dict]:
    """Merge consecutive frames sharing a label into [{start_frame, end_frame, label}], skipping None labels."""
    result: list[dict] = []
    for index, label in frame_indices_and_labels:
        if label is None:
            continue
        if result and result[-1]["label"] == label and result[-1]["end_frame"] == index - 1:
            result[-1]["end_frame"] = index
        else:
            result.append({"start_frame": index, "end_frame": index, "label": label})
    return result
