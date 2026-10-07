"""Annotated preview of an A1 analysis, drawn from the stored artifact (so what you see is what was recorded).

Draws the target box (colour = identity state), the skeleton from USABLE landmarks only (unusable joints are not
drawn or guessed), the timestamp, tracking confidence and pose-quality state. The original video is only read.
"""
import json
import subprocess
from pathlib import Path

import cv2

from app.video_ai.geometry import SKELETON

STATE_COLOR = {"CONFIRMED": (80, 200, 80), "REACQUIRED_UNVERIFIED": (0, 215, 255), "OVERLAP_AMBIGUOUS": (0, 140, 255),
               "LOST": (60, 60, 230)}
POSE_COLOR = {"GOOD": (80, 200, 80), "PARTIAL": (0, 215, 255), "LOW_CONFIDENCE": (0, 140, 255),
              "OCCLUDED": (200, 90, 200), "NOT_DETECTED": (150, 150, 150)}


def annotate(frame, record, scale):
    h, w = frame.shape[:2]
    out = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    oh, ow = out.shape[:2]
    for person in record["persons"]:
        if person["is_target"]:
            continue
        x1, y1, x2, y2 = person["bbox_norm"]
        cv2.rectangle(out, (int(x1 * ow), int(y1 * oh)), (int(x2 * ow), int(y2 * oh)), (170, 170, 170), 1)
        cv2.putText(out, f"id {person['track_id']}", (int(x1 * ow) + 3, int(y1 * oh) + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (170, 170, 170), 1, cv2.LINE_AA)
    state = record["identity"]["state"]
    target = record["target"]
    if target:
        x1, y1, x2, y2 = target["bbox_norm"]
        color = STATE_COLOR[state]
        cv2.rectangle(out, (int(x1 * ow), int(y1 * oh)), (int(x2 * ow), int(y2 * oh)), color, 3)
        cv2.putText(out, f"TARGET id {target['track_id']}", (int(x1 * ow) + 4, int(y1 * oh) + 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
    marks = record["pose"]["landmarks"]
    if marks:
        for a, b in SKELETON:
            if marks[a]["usable"] and marks[b]["usable"]:
                cv2.line(out, (int(marks[a]["x_norm"] * ow), int(marks[a]["y_norm"] * oh)),
                         (int(marks[b]["x_norm"] * ow), int(marks[b]["y_norm"] * oh)), (255, 255, 0), 2, cv2.LINE_AA)
        for item in marks.values():
            if item["usable"]:
                cv2.circle(out, (int(item["x_norm"] * ow), int(item["y_norm"] * oh)), 3, (0, 0, 255), -1, cv2.LINE_AA)
    quality = record["pose"]["quality"]
    lines = [f"t={record['timestamp_s']:.3f}s  frame {record['frame_index']}",
             f"identity: {state}" + (f" ({record['identity']['reason']})" if record["identity"]["reason"] else ""),
             "det conf {}  tracking conf {}".format(
                 "-" if not target else f"{target['detection_confidence']:.2f}",
                 "-" if not target else f"{target['tracking_confidence']:.2f}"),
             f"pose: {quality['status']}" + (f" ({quality['reason']})" if quality["reason"] else "")]
    cv2.rectangle(out, (0, 0), (ow, 78), (0, 0, 0), -1)
    colors = [(255, 255, 255), STATE_COLOR[state], (255, 255, 255), POSE_COLOR[quality["status"]]]
    for i, (text, color) in enumerate(zip(lines, colors)):
        cv2.putText(out, text, (8, 17 + 19 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    cv2.rectangle(out, (0, oh - 24), (ow, oh), (0, 0, 0), -1)
    cv2.putText(out, "A1 DEBUG: image-plane evidence only - not a score", (8, oh - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.45, (200, 200, 200), 1, cv2.LINE_AA)
    return out


def render(video: Path, frames_jsonl: Path, out_dir: Path, scale=0.5, stride=2, fps_out=30, sample_frames=True):
    out_dir.mkdir(parents=True, exist_ok=True)
    records = [json.loads(line) for line in open(frames_jsonl)]
    capture = cv2.VideoCapture(str(video))
    first = records[0]
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) * scale), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) * scale)
    movie = out_dir / "a1_annotated_preview.mp4"
    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r",
         str(fps_out), "-i", "-", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", str(movie)],
        stdin=subprocess.PIPE)
    wanted = {}
    if sample_frames:
        seen = {}
        for r in records:
            key = (r["pose"]["quality"]["status"], r["identity"]["state"])
            if key not in seen or (r["frame_index"] - seen[key]) > 400:
                seen[key] = r["frame_index"]
                wanted[r["frame_index"]] = f"{key[0]}_{key[1]}"
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % stride == 0 or index in wanted:
            drawn = annotate(frame, records[index], scale)
            if index % stride == 0:
                encoder.stdin.write(drawn.tobytes())
            if index in wanted:
                cv2.imwrite(str(out_dir / f"frame_{index:05d}_{wanted[index]}.jpg"), drawn,
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
        index += 1
    capture.release()
    encoder.stdin.close()
    encoder.wait()
    return movie
