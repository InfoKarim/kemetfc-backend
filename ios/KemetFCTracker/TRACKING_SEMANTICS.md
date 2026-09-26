# Tracking semantics (what each telemetry field is allowed to claim)

Safe "unknown" beats a confident wrong answer. A field is `null` when the system does not know it.

## Per-frame telemetry (`FrameTelemetryAssembler`)

| Field | Meaning |
|-------|---------|
| `player_bbox`, `player_center`, `player_confidence`, `player_track_id` | Present only when the tracker **committed a detection this frame** (`locked`/`reacquired` with confidence > 0). Never a stale or predicted box. |
| `tracking_status` | The tracker's real state: `locked`, `reacquired`, `temporarily_lost`, `searching`, `lost`. After the first reacquisition the tracker stays `reacquired`. |
| `identity_ambiguous` | `PlayerTracker.lastMatchWasAmbiguous`. `null` = unknown (never read as "not ambiguous"). |
| `ball_bbox`, `ball_center`, `ball_confidence` | Present only when a ball was **detected this frame**. Confidence is the detector's own (classical: 0.20–0.40). |
| `ball_track_id` | `BallTracker.currentTrackId`: stable while one ball is followed, changes when a new track starts after a loss, `null` when not detected. It is no longer a constant 1. |
| `pose_keypoints` | Only the pose `PoseAssociator` ties to the selected player (see below), otherwise absent. |
| `t_seconds` | Session elapsed time at frame capture. `sequence_number` is assigned at durable enqueue, in capture order. |

## Pose belongs to the selected player

Old defect: `detectPose` returned Vision's **first** pose for the whole frame and it was attached to the selected
player. Now `PlayerDetector.detectPoses` returns every pose and `PoseAssociator` (pure geometry: no faces, no
biometrics) picks one only if the selected player was observed this frame, at least 60% of the pose's joints lie in the
(10%-padded) target box, and that share beats any other person's box by 25 points. Two poses claiming the same player
without a clear winner, or overlapping players, yield **no pose**. The backend re-checks the same rule
(`pose_belongs_to_player`) and does not count an unverifiable pose. "Head orientation" is never called gaze.

## Ball

* Detector actually used: `ClassicalCVBallDetector` — a bright-blob heuristic, **not a trained model**
  (`ball_model_status = fallback_classical`). Cones, chalk and white socks can pass it. It now exposes every candidate.
* `BallTracker` (v2): a new track needs 2 consistent detections; implausible jumps are rejected; look-alikes that
  motion cannot separate give no detection; a static look-alike beside a followed ball is remembered as a decoy;
  after a loss a new track gets a new id. **Not** a claim of robust ball detection: that needs trained weights and
  real-video validation.

## Framing / gimbal

* `FramingCalculator.dampedCorrection` **is now in the production path** (`FramingPipeline`, used by
  `TrackingCoordinator`): dead zone + damping on the target sent to DockKit, bounded per-update step.
* The player anchors the frame: while the player is not observed the framing is **held** (a ball cannot pull the
  camera away); a low-confidence or far-away ball is ignored; ball loss never drops the player.
* DockKit system tracking stays disabled (`setSystemTrackingEnabled(false)` in `GimbalController`), so the accessory
  never picks an arbitrary person; the control-lost failsafe is unchanged.
* Reconnect is **passive** (DockKit has no connect API): `GimbalReconnect` runs at most 3 growing wait windows
  (2 s, 3 s, 5 s), stops when `.docked` arrives, is cancellation-safe, then reports the gimbal disconnected.

## Units

Nothing is in metres, m/s or km/h. All distances/speeds are normalized image units; the feature payload carries a
`measurement_context` saying so, and touch/possession numbers are labelled `proximity_heuristic_image_space`.
No pitch calibration (homography) exists yet.
