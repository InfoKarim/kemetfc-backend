# TrainingBuddy: applied ML case study

## Problem

Youth academies need affordable video analysis that turns a match into coach-
reviewed development actions without treating automated output as ground truth.

## System delivered

- Secure phone-video ingestion and local/S3-compatible private storage.
- Background pose and full-match jobs with progress, retries, and error states.
- Football player/ball tracking and explicitly reviewable candidate events.
- Human approval before development plans and drill recommendations.
- Guardian consent, linked-child authorization, exports/deletion, and audit logs.
- Scenario-based Monte Carlo forecasts labelled as projections, not guarantees.

## Scientific evaluation

The repository provides match-isolated dataset validation, content
fingerprinting, baseline comparison, per-class metrics, event-time evaluation,
release gates, and structured error outputs. Results must be inserted here only
after running on a consented, held-out dataset.

| Metric | Task-compatible baseline | Fine-tuned model | Release threshold |
|---|---:|---:|---:|
| mAP@50-95 | Pending | Pending | Reported, not sole gate |
| Player recall | Pending | Pending | 0.80 |
| Ball recall | Pending | Pending | 0.65 |
| Macro F1 | Pending | Pending | 0.70 |
| Event precision | Pending | Pending | 0.70 |
| Event recall | Pending | Pending | 0.60 |

## Honest boundary

The end-to-end software is implemented and tested. A production-quality
football checkpoint and performance claims remain blocked on representative,
consented, labelled phone-and-gimbal matches. Until then the platform is an
advanced application prototype, not a validated automated coach.

## Pilot success metrics

Processing success and latency, coach approval/override rate, false events per
match, time saved per review, plan completion, weekly engagement, and guardian
privacy-request completion. Player improvement requires a longer controlled
study and must not be inferred from engagement alone.
