# ML-1 gate review (actual code, 2026-10-08)

Reviewed `app/ml1` as it stood at `01afc8e` against the question "can a user-written JSON field stand in for real consent?".

| # | Finding in `01afc8e` | Severity | Action in this branch |
|---|---|---|---|
| 1 | `consent` (kind, purpose, dates, `covers_all_visible_children`, reviewer) was a block inside the example, written by whoever built it, and was the only evidence checked | High | Real examples now also need the **trusted registry** (`registry_adapter.SqlAlchemyConsentRegistry`, read-only) to confirm, at that moment, the consent, the approved dataset entry, the reviewer, the video checksum and the pseudonyms. No registry, or any failure to confirm, refuses (fail closed) |
| 2 | A real example could be relabelled `SYNTHETIC_FIXTURE` and skip consent altogether | High | A synthetic claim is **proven** by regenerating the example from its recipe and comparing every number; real data cannot match |
| 3 | `approval_problems` read `report.json` / `card.json` from disk; editing the report passed the gates | High | The card records the report's SHA-256; approval recomputes it, re-hashes the weights, and re-verifies every consent behind the training data against the registry |
| 4 | Consent was checked at build and train time but not when approving or releasing | Medium | Approval and release re-verify consent refs through the registry |
| 5 | Nothing stopped `train_candidate` from running once its inputs looked fine | Medium | Training is OFF unless a human sets `KEMET_ML_TRAINING_ENABLED=1` for that process; the check comes after every gate. Tests that really train are opt-in (`trains_models`) |
| 6 | Cards did not say what a model had NOT been validated for | Medium | Every card lists its one claim (reconstruction of hidden joints vs a mean-pose baseline) and what it is not validated for (dribbling skill, drill quality, player assessment, medical/psychological conclusions) |
| 7 | Leakage control depends on the player/video/assessment ids in the example | Medium | For real data those pseudonyms are now verified against the registry (salted HMAC of the registry ids), so they cannot be re-labelled to dodge the split |
| 8 | Audit log (workflow and research DB) is hash-chained | Info | Detects edits; a full rewrite with recomputed hashes is NOT detectable without an external anchor. Documented, not fixed |

## Gaps that remain (not hidden)

* `consent_ref`, `video_id` and the player pseudonym are verifiable only with the **same salt** the dataset was built with; a lost salt makes real examples unverifiable (they fail closed).
* The adapter reads whatever database the factory points at. Real read-only access is arranged by the operator (`readonly_sqlite_factory` for SQLite; a read-only role for PostgreSQL). I did not test PostgreSQL.
* Nothing yet connects dataset export to the existing ML dataset registry end to end; the adapter proves the gate, not a full pipeline.
* "Better than a mean-pose baseline at reconstructing hidden joints" says nothing about understanding dribbling or the quality of any recommendation. The approval gate checks plumbing quality only.
* The `trains_models` tests (about 16) could not be run in this task (training was not allowed), so their updated registry-aware versions are **unverified**. Everything that refuses before training is tested without training.
* `subprocess` is used once, for `git rev-parse HEAD`, with fixed arguments (bandit: low severity).
