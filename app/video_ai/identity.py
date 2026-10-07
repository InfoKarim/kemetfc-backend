"""Diagnostic-only: which tracked person is the most prominent in a clip.

This is NOT how the player is chosen in a player assessment (the coach taps them: target_selection.py, followed by
target_tracking.py). "The biggest person" is not "the player", so this result is only ever used when a developer
asks for the clearly labelled DIAGNOSTIC fallback, and it never counts as a verified identity.
"""
from app.video_ai.config import IDENTITY


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


def select_most_prominent(frames: list[list[dict]], fps: float):
    """DIAGNOSTIC ONLY. The track with the largest total confidence x area among tracks present long enough.
    Returns (frame_index, detection_index, tracks) of that track's most prominent frame, or None."""
    tracks = track_summaries(frames, fps)
    eligible = [t for t in tracks.values() if t["seconds_present"] >= IDENTITY["diagnostic_min_presence_seconds"]]
    if not eligible:
        return None
    best = max(eligible, key=lambda t: (t["weight"], -t["track_id"]))
    pick = None
    for index, detections in enumerate(frames):
        for k, det in enumerate(detections):
            if det.get("track_id") == best["track_id"]:
                area = (det["bbox_norm"][2] - det["bbox_norm"][0]) * (det["bbox_norm"][3] - det["bbox_norm"][1])
                score = det["confidence"] * area
                if pick is None or score > pick[0]:
                    pick = (score, index, k)
    return pick[1], pick[2], tracks
