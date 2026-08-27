# Football ML release checklist

No football checkpoint may be marked production-ready until every item is
complete and reviewed by a named owner.

## Data

- [ ] Purpose-specific guardian consent exists for every included recording.
- [ ] Dataset card contains collection dates, scope, exclusions, and retention.
- [ ] At least three complete matches exist; target release should use many more.
- [ ] Train/validation/test are isolated by match and leakage validation passes.
- [ ] Camera, lighting, club, age-band, and sex-cohort slices are documented.
- [ ] Annotation guide and second-review procedure are recorded.
- [ ] Inter-annotator agreement is measured on a representative subset.

## Experiment

- [ ] Dataset fingerprint and Git revision are saved with the run.
- [ ] Base model and fine-tuned model use the same untouched test set.
- [ ] Seed, image size, epochs, hardware, runtime, and package versions are saved.
- [ ] Final model selection uses validation data—not repeated test-set tuning.

## Evaluation

- [ ] mAP@50 and mAP@50-95 are reported overall and per class.
- [ ] Player and ball precision, recall, and F1 are reported.
- [ ] Tracking recall, ID switches, fragmentation, HOTA, and IDF1 are reported.
- [ ] Event precision/recall uses a documented timestamp tolerance.
- [ ] Error slices cover occlusion, distance, blur, lighting, similar kits,
      crowded areas, substitutions, and gimbal loss.
- [ ] All numeric gates in `ml/release_thresholds.json` pass.

## Product and safety

- [ ] Coaches can inspect source video and override every automated finding.
- [ ] Failure and low-confidence states do not generate approved plans.
- [ ] Model/version lineage appears in stored analyses and audit logs.
- [ ] Load, retry, rollback, backup/restore, and monitoring drills pass.
- [ ] Privacy/security and Ultralytics licensing receive independent review.
- [ ] Pilot measures accuracy, coach time saved, override rate, and user safety.
