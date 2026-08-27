# TrainingBuddy research protocol

## Registered question

Can an uncertainty-aware, single-camera phone-video pipeline produce more useful
and safer coach-review candidates for youth-football development than a generic
detector and rule-only baseline?

Primary outcomes must be fixed before the untouched test set is evaluated:
ball recall, player recall, tracking association quality, event F1, calibrated
selective risk, coach override rate, and review time per match.

## Evidence phases

1. **Feasibility:** at least three consented matches, annotation dry run, pipeline
   failure discovery. This is not release evidence.
2. **Model development:** match-isolated training and validation; tune only on
   validation data.
3. **Locked evaluation:** freeze code, checkpoint, thresholds, test manifest,
   and analysis plan before running the held-out test set once.
4. **Prospective pilot:** measure coach corrections, time saved, abstentions,
   adverse outcomes, and player/guardian experience.

## Required comparisons

- Generic detector versus football-fine-tuned detector on identical examples.
- Tracker disabled versus enabled.
- Calibration disabled versus enabled.
- Abstention disabled versus enabled, reporting both risk and coverage.
- Rule-only recommendations versus analysis-linked recommendations.

Use paired bootstrap comparison for identical test examples and report effect
sizes with intervals. Do not use repeated test-set tuning. Slice results by
camera, lighting, age band, sex cohort, club, blur, occlusion, and crowding.

## Forecast validation

Monte Carlo forecasts are scenario models until longitudinal outcomes exist.
Calibrate adherence, per-session gain, and volatility only from coach-reviewed
interventions. Report bootstrap intervals and sensitivity ranges. A future
causal study must pre-register intervention assignment and account for baseline
ability, maturation, coach, exposure, and missing outcomes.

## Reproducible run

1. Validate the dataset and save its fingerprint.
2. Save Git revision, environment lock, seed, hardware, model lineage, and
   command line in experiment metadata.
3. Train and evaluate the generic baseline and candidate checkpoint.
4. Evaluate annotation agreement, calibration, slices, paired comparison, and
   ablations using the scripts in `scripts/`.
5. Build the final report with
   `python -m scripts.build_research_evidence_report --metadata ...`.

The report intentionally treats absent evidence as missing, never as passed.
Passing code-level gates does not replace independent safeguarding, privacy,
security, licensing, ethics, or field-pilot approval.
