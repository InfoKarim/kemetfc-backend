# TrainingBuddy Computer Vision

The first vision pipeline analyzes single-player training videos with
MediaPipe Pose Landmarker. It records 33 body landmarks for sampled frames
and extracts visibility-aware movement measurements for coach review.

The generated JSON includes per-frame and summary values for knee, hip, and
elbow angles, trunk lean, shoulder and hip tilt, stance-width ratio, and
left/right knee and hip asymmetry. Normalized landmarks are scaled with the
video dimensions before angles are calculated, which is especially important
for portrait video.

## Install

```bash
python -m pip install -r requirements-vision.txt
python scripts/download_pose_model.py
python scripts/smoke_test_pose.py
```

## Run the background worker

```bash
export POSE_LANDMARKER_MODEL_PATH=models/pose_landmarker_lite.task
export POSE_LANDMARKER_MODEL_VERSION=pose-landmarker-lite-float16
export POSE_SAMPLE_EVERY_N_FRAMES=3
# Optional override. Normally the worker selects this from the queued job.
export POSE_MOVEMENT_TYPE=squat_jump
python -m app.run_video_analysis_worker
```

The worker continuously polls for queued jobs. Use `--once` for a smoke test.
It writes the raw JSON under `analysis/results`, publishes a structured
`AIAnalysisRecord`, updates the video state, and creates a draft training plan
when matching drills exist. Failed jobs and videos retain an explicit error
state for operators and coaches.

Completed jobs can be opened from the Video Library. The review page displays
pose coverage, detected movement cycles, jump phases, and repetition
measurements. A coach can approve or reject the automated result and store
review notes. Approval means the coach confirmed the output against the video;
it does not turn the measurements into a medical assessment.

## Supported movement analyses

- `squat_jump`: movement cycles, jump detection, and descent, ascent, flight,
  and landing phases.
- `agility_ladder`: left/right foot contacts, cadence, alternation, step-count
  balance, knee drive, foot lift, and posture measurements.

Choose the analysis when uploading from Add Video. The worker automatically
uses the queued job's analysis type, so `POSE_MOVEMENT_TYPE` is only needed as
an explicit override.

## Current limitations

- Designed for one visible player and a mostly fixed camera.
- Pose coordinates have not yet been calibrated for field distance or speed.
- Measurements are not calibrated football-performance scores and must not be
  presented as approved player assessments until a coach reviews them.
- Speed and real-world distance require field or camera calibration and are
  intentionally not inferred from normalized pose landmarks.
- The database-backed queue is durable and safe for one worker. Horizontal
  worker scaling requires row-level job claiming on PostgreSQL before running
  more than one worker replica.
