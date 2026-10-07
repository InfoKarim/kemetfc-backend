# KEMET FC AI video analysis - phase A1 (pose + body-movement foundation)

A1 turns one recorded video into **evidence**: who was tracked, where their body joints were, how confident that
is, which 2D angles can honestly be read from it, and how the image-plane motion changed over time. It produces no
score, no rating, no physical speed, and no medical statement.

```
video -> every frame -> person detection + tracking -> target selection -> pose on the target's box only
      -> landmark usability + anatomical plausibility -> pose quality -> 2D angles -> VIDEO_MOTION_PROXY series
      -> JSON header + per-frame JSONL + short summary (+ annotated preview drawn from the artifact)
```

Run: `python scripts/run_video_ai_a1.py --video V --out DIR --recording-id R --player-id P`
Preview: `python scripts/render_video_ai_a1_debug.py --video V --artifact DIR --out DIR2`
Compare two runs: `python scripts/compare_video_ai_a1_runs.py DIR_A DIR_B`
Tests: `pytest tests/test_video_ai_a1.py` (synthetic landmarks and detections only).

## What is reused, what is new

Reused (no second computer-vision stack): `app.pose_features` (landmark map, angle and line-tilt maths),
`app.full_match_analyzer._load_yolo` and its class vocabulary, the bundled `models/yolo26n.pt` and
`models/pose_landmarker_lite.task`, the Ultralytics ByteTrack configuration used by the match analyzer, OpenCV
decoding as in `pose_analyzer`. New code lives in `app/video_ai/`.

Not reusable as-is: `MediaPipePoseAnalyzer` (it asks for one pose in the whole frame, so with several people it can
land on the wrong one, and it samples every 3rd frame), `FullMatchAnalyzer` (streams from a file path with
Ultralytics' own decoder: no control of rotation or timestamps, no pose association).

## Rules the code enforces

* **Timestamps** come from the container's presentation timestamps (ffprobe), one per decoded frame; the run fails
  if the counts differ. Frame ids are 0..N-1 with no gaps.
* **Pose only on the target.** MediaPipe runs on the target's box (grown 15%), so a pose can never belong to
  someone else in the frame; coordinates are mapped back to the full upright frame.
* **Identity.** One fixed tracker id. If it is lost, frames are `LOST` - a new id is never adopted. The first
  sight, a reacquisition, a picture cut / appearance break, an implausible jump, and another person overlapping the
  target all put frames in an uncertain state. Segments of consecutive `CONFIRMED` frames are the only place
  anything is measured over time.
* **A landmark is used only if** visibility and presence >= 0.5, it is inside the frame, and inside the target box.
  Never filled in.
* **Plausibility.** A model can fit a whole body to a face close-up and report visibility 1.0. A pose whose box is
  wider than tall, whose torso is upside down (or two or more body links are reversed), or whose limb lengths are
  impossible is rejected as a whole; an ankle in the top of the box or a shoulder in the bottom is dropped. A body
  that is lying or inverted in the image therefore becomes NOT_MEASURABLE, not a wrong number. This assumes an
  upright camera.
* **Angles** are image-plane (2D). Each has value, unit, confidence (lowest landmark visibility used), state
  (`MEASURED` / `NOT_MEASURABLE` with a reason), definition version and space `IMAGE_PLANE_2D`.
* **Proxies** are labelled `VIDEO_MOTION_PROXY` and are in image units (frame heights, degrees, hertz). Derivatives
  use a 9-frame Savitzky-Golay filter inside one identity segment, never across a gap, never interpolated, and not on
  the first/last frames of a run. The camera is **not** compensated; the camera's own global shift is recorded per
  frame and flagged.
* **Cadence proxy:** dominant oscillation of one knee's 2D angle (Lomb-Scargle, 0.5-4 Hz, 4 s windows, needs the
  whole window confirmed and >= 90% of frames measured, and >= 60% of band power near the peak). It is not a
  validated cadence.

## Artifact

`a1_analysis.json` (header: ids, video facts, models + weight hashes, thresholds, definitions, tracks, summaries,
uncertain intervals, NOT_MEASURABLE intervals per measurement, windows, validation results, SHA-256 of the frames
file), `a1_frames.jsonl` (one object per frame: persons, target, identity, pose quality, all 33 landmarks with
usable flag, angles, motion proxy, camera), `a1_summary.md`.

## Not in A1

Metres, m/s, distance, sprint timing, ball events, pass/shot detection, KEMET FC ratings, FIFA/EA ratings, overall
or potential scores, injury risk, medical or psychological statements, re-identification of a named player.
