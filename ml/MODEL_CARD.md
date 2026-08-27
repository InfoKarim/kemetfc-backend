# TrainingBuddy football detector model card

Status: **development only—no checkpoint is approved for production**.

## Intended use

Detect football participants and the ball in phone-and-gimbal match video. The
tracker produces reviewable candidate events and image-plane movement summaries.
A coach must inspect video and approve outputs before they influence a plan.

## Out-of-scope use

Identity recognition, medical or injury diagnosis, physical distance without
calibration, contract/scouting decisions, autonomous child evaluation, or use
outside documented camera and lighting conditions.

## Evaluation required for approval

- Baseline versus fine-tuned mAP@50 and mAP@50-95.
- Per-class precision, recall, and F1 on a match-held-out test set.
- Ball recall, ID switches/track fragmentation, and event precision/recall.
- Sliced error analysis for camera, lighting, age band, sex cohort, club, and
  crowded/occluded scenes.
- Runtime and failure rate on the deployment hardware.

Default gates are stored in `ml/release_thresholds.json`. Passing a numeric gate
does not remove the human-review requirement.

## Current limitations

The generic base model is not evidence of football accuracy. Passes are
proximity-based candidates. Team identity, tactical position, shots, goals,
and physical speed require additional labels, calibration, and validation.
