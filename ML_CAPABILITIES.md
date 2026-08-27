# ML capability boundary

TrainingBuddy now has two analysis pipelines:

- MediaPipe single-person pose estimation for controlled squat-jump and
  agility-ladder recordings.
- A `full_match` YOLO tracking pipeline for football-specific checkpoints with
  `player`, `goalkeeper`, `referee`, and `ball` classes. It produces persistent
  player tracks, image-plane movement, ball-control samples, and reviewable pass
  candidates. Set `target_track_id` on a second job when a coach wants metrics
  for one selected track.

The code does **not** identify a child by face. Selecting the target is an
explicit coach decision using a tracker ID. Passes are candidates inferred from
proximity-based possession changes; they are not ground truth. Physical metres,
speed, shot detection, tactical position, and team identity require pitch
calibration and additional validated labels/models.

## Training and release gate

1. Label child-safe, consented match frames using `ml/football_dataset.yaml`.
2. Install `requirements-vision.txt` and run
   `python -m scripts.train_football_detector --device 0`.
3. Report per-class precision/recall and mAP on a held-out test set split by
   match, club, camera, age band, sex, and lighting—not random adjacent frames.
4. Do not deploy a checkpoint until player and ball thresholds are signed off
   and error review covers occlusion, substitutions, crowded boxes, and camera
   cuts.
5. Confirm the Ultralytics license is compatible with the deployment; the PyPI
   package is AGPL-3.0 unless an appropriate commercial license is obtained.

All generated results require human review. Monte Carlo development forecasts
are scenario projections using visible assumptions; they are not trained outcome
predictions and must not be described as guarantees.

## Reproducibility and evaluation

The release workflow is documented in `ml/README.md`. Dataset manifests are
validated for consent references, label bounds, missing files, duplicate images,
and match-level split leakage. Training saves the dataset fingerprint, Git
revision, fixed seed, parameters, generic baseline, validation result, held-out
test result, and baseline improvement. Independent evaluation reports per-class
detection errors, event timing errors, tracking recall, and ID switches before
applying an automated release gate.

The built-in association metrics deliberately do not claim HOTA, MOTA, or IDF1.
Use TrackEval on the final annotated tracking set for publication-standard
metrics and attach that report to the release checklist.

## Research evidence and uncertainty

Independent detection and event annotations can now be evaluated with symmetric
agreement metrics. Held-out confidence records can be evaluated for ECE, maximum
calibration error, Brier score, log loss, cohort calibration, and selective risk.
The full-match pipeline abstains from producing player scores when configured
evidence thresholds fail, and the coach review page exposes that decision.

Paired bootstrap comparison, Wilson slice intervals, ablation analysis, forecast
assumption calibration, and Monte Carlo sensitivity are implemented. Combine
their artifacts with `scripts.build_research_evidence_report`; missing evidence
is reported as missing and cannot pass a release gate.
