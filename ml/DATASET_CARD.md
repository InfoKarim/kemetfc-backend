# TrainingBuddy football dataset card

Status: **not yet populated with a release dataset**.

## Source collection mechanism

Staff can flag an existing, consented video as a dataset source candidate from
the Video Library (`/videos-dashboard`, admin only) or review flagged
candidates at `/ml-dataset-registry`. Flagging records `team_id`, `age_band`,
`sex_cohort`, `camera_id`, `lighting`, and the guardian consent on file for the
video's officially associated player — it does **not** by itself confirm
consent for every child visible in the footage. A candidate stays
`pending_review` until a named reviewer manually verifies consent coverage for
the full footage and marks it `approved`; only `approved` entries are included
in the CSV export at `/ml-dataset-entries/export.csv`.

That export is a **source-video registry**, not the frame-level
`manifest.csv` described below — it lists which raw videos may be used to
build one. Frame extraction and annotation (producing an actual
`manifest.csv` row per sampled image) still happen separately, per
`ml/ANNOTATION_GUIDE.md`, outside this app.

## Purpose

Consented phone-and-gimbal football footage for player, goalkeeper, referee,
and ball detection. Data is intended for coaching support, not biometric
identification, medical assessment, scouting guarantees, or automated decisions
about children.

## Required provenance

Every sampled image must have a row in `manifest.csv` with match, club, camera,
age band, sex cohort, lighting, guardian consent, and split identifiers. The
manifest stores consent references—not child names or guardian contact details.

## Split policy

Split by complete match. Adjacent frames from one match must never cross train,
validation, or test. Hold out clubs/cameras where sample volume permits and
publish performance by camera, age band, sex cohort, and lighting.

## Annotation policy

YOLO classes are `player`, `goalkeeper`, `referee`, and `ball`. Annotators box
visible objects, mark heavily occluded objects only when position is defensible,
and use a second reviewer for ambiguous balls and crowded penalty areas.

## Privacy and retention

Only recordings with active, purpose-specific consent may enter the dataset.
Revoke and rebuild the dataset when consent is withdrawn. Keep raw videos and
images private, encrypted, access logged, and outside Git. Do not perform face
recognition.

## Known gaps before release

- A source-registration mechanism now exists (see above), but zero videos
  have been approved through it yet — representative match and subgroup
  counts are still not available.
- No video has undergone frame extraction or annotation into `manifest.csv`.
- Inter-annotator agreement has not yet been measured.
- No trained checkpoint has passed the release thresholds.
