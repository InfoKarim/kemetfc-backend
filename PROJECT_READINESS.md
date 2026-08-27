# Project readiness assessment

Updated: 2026-08-20

## Engineering scope completed

| Area | Status | Evidence |
|---|---|---|
| Core product and APIs | Complete | Players, teams, matches, videos, analyses, plans, drills |
| Background analysis | Complete | Durable jobs, progress, failures, raw results, review |
| Child-data controls | Complete in code | Consent, guardian links, export/delete, audit |
| Deployment blueprint | Complete in code | PostgreSQL, S3-compatible storage, HTTPS config, CI |
| Pose pipeline | Complete, validation limited | Controlled single-player movements |
| Full-match pipeline | Complete in code | Player/ball tracking and candidate events |
| Dataset governance | Complete | Manifest, consent references, fingerprint, leakage gate |
| ML evaluation framework | Complete | Baseline, per-class, events, tracking diagnostics, gates |
| ML documentation | Complete | Dataset/model cards, release checklist, case study |
| Annotation agreement | Complete in code | Symmetric object/event agreement and adjudication guide |
| Confidence calibration | Complete in code | ECE, Brier, log loss, reliability bins, slice calibration |
| Safe abstention | Complete in code | Low-evidence match results cannot generate player scores |
| Research comparisons | Complete in code | Paired bootstrap, Wilson intervals, ablations, slice gaps |
| Forecast calibration | Complete in code | Coach-reviewed bootstrap assumptions and sensitivity grid |
| Evidence report | Complete in code | Conservative combined gate; missing evidence never passes |
| Trained football checkpoint | Blocked on data | Requires consented labelled matches |
| Field validation/pilot | Blocked on users | Requires academy, coaches, players, and guardians |

## Definition of 90%

For work that can be completed without inventing external evidence, the project
is at least 90% prepared: architecture, data contracts, validation, evaluation,
release gates, tests, deployment controls, and documentation exist.

The product is **not 90% scientifically validated**. That percentage cannot be
earned through more code. It requires real labelled matches, a trained model,
held-out metrics, coach review, and a pilot. Until those exist, describe the
project as a production-oriented applied-ML prototype.

## Next external milestone

Collect 10 initial consented matches from the target phone/gimbal setup, label a
quality-control subset, measure annotation agreement, and run the documented
baseline. Scale toward a representative release dataset only after reviewing
the first error report.

## Local research-foundation score

The locally implementable foundation is **10/10 categories complete**: research
boundary, governed dataset contract, leakage-safe splits, annotation agreement,
streaming baseline, uncertainty calibration, safe abstention, paired/sliced
evaluation, Monte Carlo calibration/sensitivity, and reproducible evidence
reporting. This exceeds the requested 90% software/research-infrastructure bar.

Scientific validation remains **0% complete until real evidence is supplied**
for the unchecked items in `ML_RELEASE_CHECKLIST.md`. Code must never be used to
inflate that separate percentage.
