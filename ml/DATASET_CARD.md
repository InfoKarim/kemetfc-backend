# TrainingBuddy football dataset card

Status: **not yet populated with a release dataset**.

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

- Representative match count and subgroup counts are not yet available.
- Inter-annotator agreement has not yet been measured.
- No trained checkpoint has passed the release thresholds.
