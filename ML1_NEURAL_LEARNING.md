# ML-1: neural learning foundation

This extends the platform; it replaces nothing. It prepares, under strict gates, to learn from CONSENTED, coach-reviewed
pose evidence. It trains nothing real, deploys nothing, and scores no player.

## What already existed (verified in the repository)

| Area | Found |
|---|---|
| Detection / tracking | YOLO football detector workflow (`scripts/train_football_detector.py`, `app/football_dataset.py`: manifest with consent ids, match-level splits, leakage checks), `full_match_analyzer`, ByteTrack; `ml/` model card, dataset card, release thresholds |
| Pose | MediaPipe pose analyzer, `pose_features` (angles), squat-jump / agility analysers; the A1 pipeline `app/video_ai` (identity states, plausibility gates, 2D angles, motion proxies, JSONL evidence) |
| Records | `tracking_sessions`, samples, feature sets; `player_assessments`; V1 `v1_*` tables; `coach_validation_labels` (coach labels kept apart from AI values, "future ground truth") |
| Consent | `guardian_consents` with purposes including `ml_training`, withdrawal and expiry; `ml_dataset_entries` (flagged -> `pending_review` -> `approved` after a named reviewer confirms consent for EVERY visible child) |
| Models | `ml_model_registry` (experimental/active, rollback by status), `ModelRegistryService` |
| Evaluation | `app/ml_evaluation.py` (detections, events, tracking, release gate), uncertainty/calibration, research evidence |
| Drills | `drills` table, `drill_ranking`, `drill_recommendations` (weakness scores, no history, no time budget, no reasons) |
| Missing | Any neural network or PyTorch code (torch is only present as an Ultralytics dependency, not in `requirements.txt`); any pose-sequence dataset; any controlled retraining workflow. Also: `ml/README.md` says `datasets/football/` is git-ignored, but `.gitignore` had no `datasets/` rule (fixed here). |

## What this adds (`app/ml1/`, tests `tests/test_ml1_*.py`, `requirements-ml.txt`)

* `dataset.py` versioned pose-sequence datasets; `consent.py` eligibility gate + pseudonyms + blocked sources;
  `model.py` the temporal CNN; `train.py` the pipeline; `registry.py` versions, approval, release markers;
  `workflow.py` the controlled learning loop; `development.py` and `recommendations.py` the future modules;
  `synthetic.py` clearly labelled fixtures. `scripts/ml1_pipeline_check.py` runs the plumbing on synthetic data.

## Dataset schema (`kemet.ml1.pose_sequence/1`)

One example = one window of 48 frames at 15 Hz (3.2 s) taken only inside an unbroken LOCKED identity run from A1.

Metadata: `anonymous_player_id` (salted HMAC pseudonym, salt kept outside Git), `assessment_id`, `video_id`, `drill_type`,
`target` (label, source `COACH_TAP`, epoch; no biometrics), `window` (frames, seconds, segment, epoch),
`ball_interaction` (`NOT_AVAILABLE` until A-ball exists), `labels` (type, value, status `COACH_REVIEWED`, reviewer ref),
`model_confidence` (tracking, keypoint, observed fraction), `consent` (kind, purpose, consent ref, granted/expires/withdrawn,
dataset entry status, `covers_all_visible_children`, reviewer ref), `provenance` (source, recording id, source video
SHA-256, pipeline version, `third_party_content`).

Arrays: `keypoints (T,13,3)` torso-normalized x, y and confidence; `keypoint_mask`; `angles (T,10)` + mask (the A1 2D
measurements); `motion (T,4)` + mask (centre velocity, tracking confidence, camera moving); `t`. A joint that was not usable
is a masked zero, never interpolated or estimated. Datasets are `manifest.json` + `arrays.npz`, fingerprinted; raw
videos, paths and pixels are never stored. `datasets/`, `models_ml1/` and `*.npz` are git-ignored.

## Consent enforcement (checked at build, at load, at split, at train, and at workflow approval)

A real example needs: guardian consent kind with purpose `ml_training`, granted, not withdrawn, not expired; a dataset
entry `approved`; a reviewer who verified the consent covers every visible child; the target chosen by the coach's tap;
labels from a coach review only. Third-party content is never eligible. The Instagram reel (`7228B688...`, SHA `429b4577...`)
and the TV recording (`EA8BCAB9...`, SHA `d68d9be3...`) are on a blocklist that wins over any claimed consent. Synthetic
and real examples cannot share a dataset. A withdrawal after the fact makes loading and training refuse and marks every
model trained on that consent INVALIDATED (never deleted).

## The network

`PoseTemporalCNN` (kemet.ml1.pose_tcn/1): 1x1 stem, three residual dilated 1D-conv blocks (dilation 1, 2, 4), masked mean
pooling over frames with a usable pose, a 32-d movement embedding, a reconstruction head used for training, and an optional
classifier. 34,954 parameters at the default size. Input channels carry every value with its mask.
Training is self-supervised first: hide 25% of the OBSERVED joints, predict them, score only those, compare with a
per-joint mean-pose baseline. A drill classifier exists only if at least 2 classes each have 30+ coach-reviewed examples
from 3+ players in the training split; otherwise the report says why. Deterministic on CPU for a given dataset, split and seed.

## Training pipeline

dataset -> validate (shapes, NaN, masks, timestamps, ranges, consent) -> group split (players, source videos and
assessments that share anything form one group and land in one split; too few groups -> refuse) -> train -> evaluate
train/val/test against the baseline -> versioned candidate (`vNNNN-<dataset fp>-s<seed>`, weights SHA-256, report with dataset
fingerprint, split hash, seed, git revision, torch version, consent audit, leakage check).

## Controlled learning

new video -> analysis (ANALYSED) -> coach review -> approved example (consent checked) -> dataset version -> a NAMED HUMAN
requests training (single-use) -> candidate -> gates -> a NAMED HUMAN approves -> a NAMED HUMAN records release. An upload or an
analysis only records ANALYSED. There is no scheduler, upload hook, network call or deployment code. The audit log is
append-only and hash-chained. A model trained on synthetic data can never be approved; approval also needs 50+ test
examples, a 10% gain over the baseline, a passing consent audit and leakage check.

## Player development (design + pure rules)

Two measurements compare only if player, drill, protocol, metric, unit, definition version, analysis/model version, camera
orientation and capture stability all match. A change counts only if it exceeds 2x the combined measurement uncertainty (and
any minimum that matters); unknown uncertainty is INSUFFICIENT_EVIDENCE. "Improved/declined" appears only where a coach agreed
the metric's direction. No rating, no overall score.

## Training recommendations

Explainable rules (`EXPLAINABLE_RULES_V1`): only coach-reviewed weaknesses; active, age-appropriate drills; equipment
available; fits the session time; rest after recent practice; variety bonus; optional level cap; every inclusion and exclusion
has a stated reason. `validated: false`, `neural_network_used: false`. No neural recommendation is claimed or used.

## Limitations

No real consented dataset exists, so nothing has been trained on real movement. Synthetic results prove plumbing, not
football. Ball interaction is a placeholder. Pose quality is limited to what A1 can measure (image-plane, single camera).
The group split needs at least 5 independent player/video groups. The dataset registry, consent and labels live in the
existing database; this package reads them as exported records and does not yet connect to it.

## Next step

Collect a small, consented, coach-reviewed set of real single-player clips (via the existing ML dataset registry), run A1
with the coach's target selection, export examples through `dataset.build_from_a1`, have the consent reviewer verify each, and
only then run a human-requested training and evaluation on real data.
