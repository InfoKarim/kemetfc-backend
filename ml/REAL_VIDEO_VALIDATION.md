# Real-video validation (next step — nothing here has been run on real footage yet)

Every accuracy claim about tracking must come from **human ground truth on real clips**, measured by
`app/services/tracking_validation.py`. Until that has been done, the honest status of player tracking, ball
tracking and pose association is *"behaves correctly on synthetic scenarios"* (unit tests), not
*"accurate"*. This document is the entry point for doing it.

## Procedure

1. **Record clips** with the iOS app (Smart Soccer mode) at the real gimbal/phone setup. Cover, on purpose:
   same-kit players crossing and overlapping, the target leaving and re-entering the frame, occlusion by another
   child, distant/fast ball, cones/chalk/white socks near the ball, indoor and outdoor, different lighting.
   Only use clips whose players' guardians have consented (see `DATASET_CARD.md`).
2. **Annotate** each clip at a fixed rate (e.g. 5 fps) as JSON (below). Two people should annotate a sample and
   `scripts/evaluate_annotation_agreement.py`-style agreement should be checked before trusting the labels.
3. **Export the session's telemetry**: `GET /tracking/sessions/{id}/samples` (coach/admin, own session; page with
   `after_sequence`), saved as `samples.json`.
4. **Run**

   ```bash
   python scripts/validate_tracking_session.py --telemetry samples.json --ground-truth gt.json --out report.json
   ```

5. Read `report.json`. Set `metadata.source` to `"real_video"` in the ground truth **only** for genuine footage; the
   report then says `evidence: "real_video"`, otherwise `"not_real_video"`.

## Ground truth format

```json
{
  "metadata": {"source": "real_video", "clip": "u10_scrimmage_03.mov", "annotator": "coach_a", "notes": ""},
  "frames": [
    {"t_seconds": 12.0, "target_visible": true, "target_bbox": [0.41, 0.30, 0.12, 0.40],
     "ball_visible": true, "ball_center": [0.62, 0.71]}
  ]
}
```

Coordinates are normalized, top-left origin. `target_bbox` is required when `target_visible`, `ball_center` when
`ball_visible`. `t_seconds` must be on the same clock as the telemetry's `t_seconds` (session elapsed time).

## What the report measures

| Area | Measured |
|------|----------|
| Selected player | `selected_player_retention`, `tracked_wrong_person_frames`, `false_id_switch_events`, `false_id_switch_frame_ratio` |
| Reacquisition | per loss: latency, correct or false; `still_lost_at_end` |
| Ball | true/false positives, false negatives, precision, recall, detections at a wrong location, ball track-id changes while visible |
| Pose | pose on target vs on another person, pose absent while the target is visible |
| Telemetry | missing / duplicate sequence numbers, sequence-vs-time order violations, max time gap, sample rate |

Counts always come with denominators; a ratio is `null` when its denominator is 0. There is deliberately no single
"accuracy" number.

## Known limits of this harness

* It measures **telemetry**, so it validates what the device decided while recording. To evaluate the trackers on
  a pre-recorded clip you must play it back through a camera-fed run (or add a replay path); replaying detector output
  through `PlayerTracker` offline is not built yet.
* Pose association is checked geometrically (joints inside the annotated target box), not against annotated skeletons.
* Tolerances (`target_tolerance_box_fraction`, `ball_tolerance`, `match_window_seconds`) are parameters recorded in the report.
