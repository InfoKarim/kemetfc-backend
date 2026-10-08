# KEMET AI Learning Engine V1 - fixes after the review of `699a312`

Date: 2026-10-08. Branch `learning-engine-v1-fixes` in the worktree `/Users/karim/Downloads/kemetfc-learning-engine`, created from
`699a312` (`learning-engine-v1`). Nothing was pushed, deployed or scheduled. The review file
(`.../outputs/review-699a312/REVIEW.md`) was treated as a list of claims to reproduce, and all four code defects were reproduced with
fakes before any change (results below).

## Git state

| | Before | After |
|---|---|---|
| Branch / HEAD | `learning-engine-v1-fixes` @ `699a312`, working tree clean | same branch, HEAD still `699a312`; all changes are in the working tree, **not committed** |
| Files | - | 26 modified + 3 new test files + this report; every path is under `learning_engine/`, `app/ml1/`, `app/routers/learning_review.py`, `tests/`, `ML1_GATE_REVIEW.md` |

Forbidden paths: `git diff --name-only 699a312` plus untracked files, filtered to exclude the allowed set above, lists **nothing**: no
camera, upload, tracking, telemetry, DockKit, iOS, production-data or deploy file changed. The main checkout
(`/Users/karim/Downloads/cloud_platform 4`, `main` @ `567f40f`) still shows exactly the same uncommitted work as before and was not opened for writing.

## Reproduced before fixing (fake transports, temporary databases)

1. Request cap 1, first request answered HTTP 504 (retried), second answered 200: **2 requests sent**, ledger `['released','settled']`.
2. A model whose guardian consent was withdrawn: `load_model` returned the model, `invalidated.json` was never created, while the
   registry itself reported `CONSENT_WITHDRAWN`.
3. A refresh returning a new title: stored title unchanged, `verified_at` advanced by 30 days.
4. An approved source that became not-public: marked excluded, but the approved item stayed in the export.
5. `covers_all_visible_children`: read from the example in `consent.py` (confirmed by reading the code).

## Per problem: cause, fix, regression test

### 1. AI request cap and retries
* **Cause.** `released` rows were excluded from every count, and a retryable status (429/500/502/503/504) was treated as "not processed":
  the row was released and the request sent again, so a request that reached the provider disappeared from the request cap and from the cost reservation.
* **Fix.** Schema 2: ledger statuses `reserved`, `sent`, `settled`, `uncertain`, `rate_limited`, `released`. The row is committed as `sent` *before*
  the transport call and every non-released row counts against requests, tokens and cost. HTTP 5xx/504, timeouts, lost connections,
  unreadable answers and other statuses become `uncertain` (reservation kept) and are never retried. Only a 429 with a usable
  `Retry-After <= max_backoff_s` is retried, at most `max_retries` times, each time as a new counted reservation, with pacing (`min_interval_s`,
  per provider, persisted) and bounded backoff. `release_ai` works only for a request proven never sent (local https/host refusal), clears the marker
  and writes an audit event. A schema-1 database is upgraded in place and every schema-1 AI row that was not plainly settled is counted as `uncertain`.
  The daily run will not send a second request for a provider on the same day after an uncertain or in-flight one. Reports show attempted / sent /
  counted / settled / uncertain / rate-limited / in-flight / released-not-sent per provider.
* **Tests.** `tests/test_le_ai_request_cap.py` (21): cap 1 + 500/502/503/504/529 blocks the second send; 504 keeps tokens/cost and is not retried even with
  `max_retries=2`; timeout/connection/oversize/redirect are uncertain; `NOT_HTTPS`/`HOST_NOT_ALLOWED` release with audit and do not count as sent; a row already marked
  sent cannot be released; the `sent` marker is committed before the transport; the cap survives a restart and a crash after sending; 12 concurrent runs send at most 3;
  429 retry only with Retry-After, bounded by `max_retries`, by the backoff cap and by the request cap; fake-clock tests for backoff and pacing; the report fields; the schema-1 upgrade.
* **Official pages (read 2026-10-08).** Anthropic errors page: 429 rate limit with `retry-after`, 500 retry with backoff, 504 "timed out while processing". OpenAI
  error-codes page: wait at least Retry-After on a rate-limit 429; quota 429s are not helped by retrying. **Neither page says whether a failed request is billed or processed**,
  so no failed request is assumed free. YouTube quota accounting was not changed, so the Google quota page was not re-checked.

### 2. Consent when a model is loaded or used
* **Cause.** `registry.load_model` verified the architecture, a local `invalidated.json` and the weights hash only. `invalidate_for_withdrawn_consent` had to be run by someone for the file to exist.
* **Fix.** `load_model` is removed (it raises with an explanation if any old caller remains). `inspect_candidate` is integrity only. `load_model_for_use(directory, registry=, purpose=, now=)`
  is the only way to obtain a model: for a real-data model it re-verifies, on every call and before loading weights, that the consent list exists, is non-empty and well formed and that the trusted registry
  confirms every consent. Missing or unreachable registry, a registry that returns something other than a list, an edited report (hash recorded on the card), a card that disagrees with its report about real/synthetic,
  and an unapproved model for `USE` all refuse. A synthetic model may be evaluated (scope `SYNTHETIC_EVALUATION_ONLY`) and never used. `invalidated.json` can only add a refusal. `approval_problems` rejects an empty consent list too.
* **Tests.** `tests/test_ml1_model_use_gate.py` (18, JSON-only model directories, the integrity step replaced by a stand-in so nothing is trained or loaded): valid consent allows evaluation and use; withdrawal or expiry
  after approval blocks use **without** an `invalidated.json` and without writing any file; registry missing / raising / returning garbage / database table missing fails closed; empty, missing, malformed refs refuse (also in approval);
  unknown consent refused; edited report; origin mismatch; approval needed for use; synthetic rules; manual invalidation only adds a refusal; no caller of the removed loader remains. The updated `trains_models` tests were edited but not run.

### 3. YouTube refresh and retention
* **Cause.** The refresh parsed the title but stored only duration, privacy and kids flag, and always advanced `verified_at`; a purge kept video id, channel id and link of approved items forever.
* **Fix.** A refresh replaces title, publication date, duration, privacy, kids flag and fingerprint from the current response, and moves `verified_at` only for a complete valid response; partial or malformed answers change and renew nothing.
  Fingerprint clashes between canonical sources keep both rows and all items (clash recorded, audited, re-evaluated later); duplicates follow their canonical or become candidates. Past 30 days without a good refresh
  everything from the API is deleted, **including video id, channel id and link**; items with coach content or a decision are kept as `source_state='removed'` (history kept, recorded links replaced by a marker, legacy ids that embed the video id replaced),
  bare candidates are deleted; checkpoints of finished or old runs no longer keep video ids. New item ids are random. The official policy text (III.E.4.d/e) requires deleting or refreshing after 30 days; it does not say that a bare id may be kept, so the safe reading was applied. **This is not a legal-compliance certificate.**
* **Tests.** `tests/test_le_source_refresh_invalidation.py`: title change visible with date/duration/fingerprint; four kinds of partial response change nothing; repeated partial refreshes cannot extend validity; clash and duplicate handling;
  after the window a full-database dump contains no video id, link or title while coach content and decisions remain; legacy id replacement; item ids contain no video id; a removed-source item can still be edited and decided.

### 4. Approved item whose source is no longer valid
* **Cause.** `_apply_details` changed only the source row; `approved_content()` looked only at `review_status`.
* **Fix.** Approved sources are re-checked on every live run. Private, deleted/not returned, made for kids, channel change, live, implausible duration or exclusion put the approved item in `source_state='review_required'` (reason, time, audit event);
  the decision, content and history stay; the item leaves the export; `get()` shows the current source status separately from the last decision. Becoming public again does not restore anything. `decide` accepts such an item again only from a coach/admin,
  on the current version, with the source confirmed available and its link recorded; rejection is always allowed. CLI `list --needs-source-review`, API `?needs_source_review=true`, daily report counts.
* **Tests.** Six invalidation reasons (history preserved, export excluded, audit), no automatic re-approval, permissions/version/source conditions, rejection, never-approved items not flagged, CLI and backend API (guardian gets 403, a refusal is 409).

### 5. `covers_all_visible_children`
* **Finding.** The platform registry holds no per-child consent records for bystanders. The only related record is `MLDatasetEntryDB`: "approved" means a named reviewer confirmed coverage of every visible child (its own docstring), with reviewer and date.
* **Fix.** The example's field is no longer read at all (neither blocks nor helps). The adapter now also requires the review to be dated after the entry was flagged and after the consent was granted, and not in the future, in addition to the existing checks
  (approved status, named reviewer matching the pseudonym, consent being the entry's consent, player and checksum). Documented in `registry_adapter.py`, `consent.py` and `ML1_GATE_REVIEW.md`.
* **Tests.** In `tests/test_ml1_registry_gate.py`: a claim of `True` cannot open the gate against a pending or unreviewed entry; the field is ignored both ways; review-date rules; the old tests that relied on the field now test the dataset-entry status.
* **Superseded by round 2 below (a general approval is no longer accepted as coverage).** This code cannot see who is in a video and **cannot confirm that a reviewer actually checked every visible child**; with an approved, named, correctly dated entry the gate relies on that person's attestation.

## Checks run

* Full suite, training switched off (`env -u KEMET_ML_TRAINING_ENABLED -u KEMET_ALLOW_MODEL_TRAINING_TESTS pytest`): **1803 passed, 19 skipped, 0 failed** in 186 s (2 FastAPI deprecation warnings). The skips are the opt-in training tests and other pre-existing skips, not new ones.
* New test files: `test_le_ai_request_cap.py` 21, `test_ml1_model_use_gate.py` 18, `test_le_source_refresh_invalidation.py` 24 (all passed). Learning-engine tests (`test_le_*`) 195 passed before the API cases were added; the ML-1 non-training tests pass.
* `ruff check` on the changed packages and tests: all checks passed. `bandit -r learning_engine app/ml1 app/routers/learning_review.py`: 0 medium/high; 3 low, all the pre-existing `git rev-parse` subprocess call.
* The test harness (`tests/conftest.py`) forces scratch databases and directories and checks real data is unchanged; no test wrote outside temporary directories.

## What was not tested or could not be confirmed

* No live YouTube, OpenAI or Anthropic call was made, so response shapes are those of the documentation, not observed. The OpenAI `usage` field names remain unconfirmed (a reply without recognised usage is uncertain).
* The 16 `trains_models` tests (including the ones edited for `inspect_candidate` / `load_model_for_use`) were skipped on purpose; I cannot confirm they pass. Everything the fix 2 decision depends on is tested without weights.
* PostgreSQL, systemd, a real read-only database role, and the review API in a real UI were not tested.
* Whether a refused or failed provider request is billed is not documented by either provider; the code assumes it might be.
* Audit entries written by a version-1 database may contain an item id with a video id; they are not rewritten (hash chain). No live run ever existed, so this concerns test data.
* The coach's own notes are not scanned for pasted YouTube links.

## Remaining gaps before any live use or real-model use

* No channel allowlist, no key, no price table and no approved budget exist; the owner must supply and approve them.
* Someone responsible must decide the retention policy against the current YouTube terms (this work applies the safe reading only).
* Real-model use needs real consent records and a trusted reviewer attestation per video, a registry connection with a read-only role, and the dataset salt; bystander consent is not machine-verifiable.
* Re-check the providers' error and pricing pages at enable time.


---

# Round 2 (review of the working tree, 2026-10-08): two remaining points

Same worktree and branch (`learning-engine-v1-fixes`, HEAD `699a312`); all work, round 1 included, is still uncommitted in the working tree
(35 changed paths now). The main checkout was not touched. No file outside `learning_engine/`, `app/ml1/`, `app/routers/learning_review.py`,
`tests/` and the two documents changed. There is no `AGENTS.md` in the repository.

## 1. Retention restored the validity of an invalidated item

* **Reproduced before the fix.** approved -> source private -> item out of the export -> source deleted by retention: the export contained the item again
  (`['SRC-2040235c2ec8b9db'] == []` failed), with `source_issue` erased and no new decision.
* **Cause.** One column, `source_state`, held both "is the approval still valid" and "does the engine still hold the source data". Retention overwrote
  `review_required` with `removed`, and the export only excluded `review_required`.
* **Fix.** Schema 3 separates them: `approval_state` (`current` / `review_required`) and `source_state` (`present` / `removed`). Invalidation sets the first;
  retention sets only the second and no longer touches `approval_state`, `source_issue` or `source_issue_at`. Export requires `approval_state='current'`. `get()`,
  the API and the CLI show `approval_state`, `needs_source_review` and `valid_for_export` (the CLI marks `NEEDS_REVIEW(reason)` and `SOURCE_REMOVED`). The decision history,
  coach content and the invalidation reason/date survive; no video id, channel id, link or title does. A returning source, a deleted source or edited content never restores
  the approval. Approving content whose source is gone needs an explicit coach/admin decision on the current version with written reasons; it is stored as
  `review_basis = INDEPENDENT_CONTENT_SOURCE_REMOVED` and displayed as an independent review of the coach's own content, not a check of the deleted source.
* **Source expired but never invalidated** (documented, tested separately): the approval stays current and the export flags `source_removed`; retention alone is not evidence that the content went bad.
  This is a choice, not a verification.
* **Migration** (schema 2 -> 3, one transaction, refuses and rolls back on broken references, temporary databases only in tests): a version-2 item in `removed` state is checked against the audit log;
  if the log shows an invalidation after the last approval it becomes `review_required` with that reason and date; if the log cannot show the item was still valid (no approval event, or a broken chain) it becomes
  `review_required` / `UNDETERMINABLE_LEGACY_STATE`. It is never assumed valid. Version 1 databases upgrade straight to version 3.
* **Tests.** `tests/test_le_invalidation_survives_retention.py` (11): private, deleted and made-for-kids each followed by retention stay out of the export with reason, date, content and history kept and no identifier left in a full dump;
  repeated purges change nothing; a returning source or an edit does not restore; re-approval permissions/version/reasons and its label; the never-invalidated case; CLI and API output; three migration tests.

## 2. Consent of every visible child

* **Finding.** The registry holds one consent (the linked player's) and a general `approved` with a reviewer. Neither enumerates the other people visible in a video, and `MLDatasetEntryDB`'s docstring describes
  the approval as a reviewer's confirmation but the fields and the review path do not separate it from a general approval. Round 1 accepted it as an attestation; that was too strong and is withdrawn.
* **Fix.** `registry_adapter` gains a coverage interface: `CoverageRecord` (per video and per file SHA-256: scope `ALL_VISIBLE_PERSONS_ENUMERATED`, the consent ids of every visible person, named reviewer, date) read from a
  `CoverageEvidence` store. `SqlAlchemyConsentRegistry` defaults to `NoCoverageEvidence`, because **no such store exists in the platform**, so every real example is refused with
  `VISIBLE_CHILD_CONSENT_COVERAGE_NOT_VERIFIABLE`. The interface accepts no boolean; a record must name this video and this file version, include the linked player's consent, have a reviewer and a non-future date, and every listed consent is
  checked now for withdrawal, expiry, purpose and review date. Datasets carry a per-video coverage lineage into the training report; `approval_problems` and `load_model_for_use` re-verify it. A real model whose report has no lineage
  (every artifact made before this change), a registry without the coverage method, an unreachable store or an invalid record is refused. `covers_all_visible_children` is not read, as before.
* **Tests.** `tests/test_ml1_visible_child_coverage.py` (23): the players own consent plus an approved entry is not enough; the claim (True/False/other) never opens the gate; missing, failing or flag-shaped stores refuse;
  records for another video, another file version, wrong scope, no consents, missing player consent, no reviewer, future date, unknown consent all refuse; withdrawal or expiry of a second child's consent refuses; dataset build,
  load and training refuse without evidence (nothing written); model approval/use refuse without lineage, without a coverage method, with stale lineage, and after a withdrawal inside the coverage although the training player's consent is fine.
  The success paths use `MockCoverageEvidenceNotRealConsent`, a stand-in that proves nothing about real consent. Existing registry tests got the same stand-in so their success paths still test the other checks.

## Checks run (round 2)

* Focused: `test_le_invalidation_survives_retention.py` 11 passed; `test_ml1_visible_child_coverage.py` 23 passed.
* Affected (all `test_le_*` and `test_ml1_*`, training switched off): **346 passed, 16 skipped, 0 failed** in 23 s. The 16 skips are the opt-in `trains_models` tests, which I did not run and cannot confirm.
* `ruff check` on the changed packages and tests: clean. `bandit -r learning_engine app/ml1 app/routers/learning_review.py`: 0 medium/high, the same 3 pre-existing low findings (`git rev-parse`). `git diff --check`: clean.
* The full application suite was **not** re-run in round 2 (it passed in round 1: 1803 passed, 19 skipped). The review's wider run was interrupted without a cause I could establish; I did not diagnose it and do not claim the cause. Round 2 touched only the packages above.

## Not tested / cannot confirm

* I cannot confirm that any real consent or any real coverage is verified: there is no coverage store, only the interface and a stand-in.
* The 16 `trains_models` tests, PostgreSQL, systemd, a real read-only database role, real YouTube/OpenAI/Anthropic calls, and a migration of any real database were not done.
* The legacy-state rule for version-2 items rests on the audit log; a log that was rewritten consistently could not be detected.

## Are real data fail-closed without coverage evidence?

Yes for the paths in this package: `verify_example` (dataset build, load, training gate), `approval_problems`, `release_marker` (through `approval_problems`) and `load_model_for_use` all return
`VISIBLE_CHILD_CONSENT_COVERAGE_NOT_VERIFIABLE` when no trusted coverage record exists. I did not search for other code outside `app/ml1` that could read real examples, so I cannot confirm that no other path exists.

## Design still needed before any real use

* A trusted coverage store: who enumerates the people visible in a video, how their guardian consents are linked, how the decision is reviewed and audited, how it is invalidated when the file changes.
* Real consent data and a read-only registry connection; the dataset salt.
* Re-making any real model: older artifacts have no lineage.
* A decision on whether an item whose source merely expired (never invalidated) should stay exportable.


---

# Round 3: schema 1 -> 3 upgrade

* **Problem (reproduced by the reviewer, and by the new tests failing without the fix: 8 of 12).** `_upgrade_from_1` (`learning_engine/store.py`) copied `review_status='approved'` and left
  `approval_state` at its default `current`, without looking at the source. A version-1 database holding an approved item whose source was `excluded` (for example `NOT_PUBLIC`)
  became an item that `approved_content()` exported.
* **Fix.** In the same transaction, after the copy: an approved YouTube item keeps `approval_state='current'` only if its source row is recorded as `available`. A source that is
  excluded, unavailable, unknown or missing suspends it (`review_required`) with the recorded `exclusion_reason` and `verified_at`, or `UNDETERMINABLE_LEGACY_STATE` (and no date) when none
  was recorded. An old `REVIEW_APPROVED` audit event never overrides this. One audit event (`LEGACY_APPROVALS_SUSPENDED_AT_UPGRADE`, count only, no ids) is written. A dangling source
  reference still fails the foreign-key check and rolls the whole upgrade back. The 2 -> 3 path is unchanged. The export policy for a source that merely expired without an earlier
  invalidation is unchanged (product decision, out of scope).
* **Tests** (`tests/test_le_schema1_upgrade.py`, 12, temporary SQLite files): excluded / unavailable / unknown (five reason cases) suspended and not exported; only approved+available keeps approval, pending and rejected untouched;
  dangling reference refuses with the file left at version 1; coach content, versions, decisions and the audit chain preserved, approval event not restoring validity; reopening three times changes nothing and logs the event once;
  injected failure rolls back and a later healthy open upgrades; old AI rows still count (v1) and a v2 database still upgrades with its sent AI row counted.
* **Results.** New file 12 passed. Migration/retention group (`test_le_ai_request_cap`, `test_le_invalidation_survives_retention`, `test_le_config_store`, `test_le_schema1_upgrade`) 83 passed.
  Final run once: `test_le_*` + `test_ml1_*` with training disabled, fake transports only, `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1`: **358 passed, 16 skipped (the opt-in `trains_models` tests, not run), 0 failed**, 23 s. This
  setting limits resources; it does not explain any earlier slowness. `ruff` clean; `bandit` 0 medium/high (3 pre-existing low `git rev-parse` findings); `git diff --check` clean.
* **Not tested / cannot confirm.** Live YouTube/OpenAI/Anthropic calls, PostgreSQL, systemd, any real database, the 16 training tests, the full application suite in this round, and any real consent or coverage. Passing tests are code guarantees, not production readiness.
