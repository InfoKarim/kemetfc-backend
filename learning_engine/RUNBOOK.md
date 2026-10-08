# Learning Engine V1 - short runbook (local, offline)

Three different things, kept apart:

| | Status |
|---|---|
| **Local engine** (config check, offline daily run, coach review, export, reports) | Runs now. Tested with fakes. |
| **Live search** (YouTube API, real channel, real key) | **Not started.** Needs the steps in "Before a first live search". |
| **A movement-learning model on real data** | **Not trained and locked.** Needs a trusted coverage store and valid consents (see the last section). |

All commands run from the repository root. Use a private, absolute state directory (it must not be inside the repository), e.g. `STATE=/Users/you/kemet-le-state`.
The reviewer id you type is **only text** (`local_cli_unauthenticated`); the CLI prints a notice saying so. For a real audit trail use the backend review API
(authenticated coach or admin, `LEARNING_ENGINE_REVIEW_API_ENABLED=1` and `LEARNING_ENGINE_STATE_DIR`).

```sh
CFG=learning_engine/config.next-phase.template.json     # offline, no network, no channels, no providers
python3 -m learning_engine --config $CFG --state $STATE validate-config
python3 -m learning_engine --config $CFG --state $STATE run                  # daily run; offline = no discovery, writes a report
python3 -m learning_engine --config $CFG --state $STATE list --status pending
python3 -m learning_engine --config $CFG --state $STATE list --needs-source-review
python3 -m learning_engine --config $CFG --state $STATE show ITEM_ID         # content, source status, history
python3 -m learning_engine --config $CFG --state $STATE save-content ITEM_ID --file drill.json --reviewer-id NAME
python3 -m learning_engine --config $CFG --state $STATE decide ITEM_ID --version N --decision approved --reviewer-id NAME --reasons "at least 10 characters" --source-checked LINK
python3 -m learning_engine --config $CFG --state $STATE decide ITEM_ID --version N --decision rejected --reviewer-id NAME --reasons "at least 10 characters"
python3 -m learning_engine --config $CFG --state $STATE export-approved      # read-only JSON; a coach carries it into the drill library by hand
python3 -m learning_engine --config $CFG --state $STATE status
```

* `save-content` writes a NEW version (the first coach version is 2). `decide` must name the current version.
* The daily report is `STATE/reports/YYYY-MM-DD-<mode>.md` and `.json` (mode is `offline`, `live` or `fixture`). Read it with any viewer; `run` also prints the JSON.
* `run` exits 0 for `SUCCESS` and `OFFLINE_NO_DISCOVERY`, 1 otherwise. A second `run` on the same day does not repeat a finished day.
* `export-approved` lists only approvals that are still valid; an item whose source went bad is excluded until a coach decides again.
* Nothing here publishes to players, changes a programme, or touches the application database.

## Local demonstration (fixture data, not a live search)

```sh
python3 tests/demo_fixture_run.py /absolute/NEW/empty/scratch/dir
```

A fake transport answers instead of YouTube; channel and videos are marked `[DEMO]`; the report says `fixture_data_not_live_results`. The review and export steps go through the real
CLI. Nothing is written outside the directory you give; nothing enters the player library or any training dataset.

## Knowledge library, recommendations, results (local; see KNOWLEDGE_PIPELINE.md)

```sh
python3 -m learning_engine --config $CFG --state $STATE queue                       # every source and its state (discovered ... failed)
python3 -m learning_engine --config $CFG --state $STATE media-register /abs/file.mp4 --rights-basis OWN_RECORDING --grantor "who allows it" --minors no --recorded-by NAME [--queue-id Q-...]
python3 -m learning_engine --config $CFG --state $STATE pipeline-run                # one bounded pass; says ANALYZER_NOT_CONFIGURED until an analyzer is plugged in by code
python3 -m learning_engine --config $CFG --state $STATE drafts                      # drafts waiting for review, one line each
python3 -m learning_engine --config $CFG --state $STATE draft ITEM_ID               # timed notes / analyzer suggestion / coach decisions, apart
python3 -m learning_engine --config $CFG --state $STATE library                     # videos knowledge was extracted from
python3 -m learning_engine --config $CFG --state $STATE rights-withdraw MED-... --reason "..." --by NAME
python3 -m learning_engine --config $CFG --state $STATE recommend --context player.json --requested-by NAME
python3 -m learning_engine --config $CFG --state $STATE rec-list                    # re-checks validity every time
python3 -m learning_engine --config $CFG --state $STATE rec-decide REC_ID --decision approve --reviewer-id NAME --reasons "..."
python3 -m learning_engine --config $CFG --state $STATE outcome-add --player ALIAS --date 2026-10-09 --executed yes --recorded-by NAME --rec-id REC_ID --measure clean_runs_30s --unit count --before 5 --after 7 --method-before "..." --method-after "..." [--conditions "..."]
python3 -m learning_engine --config $CFG --state $STATE outcomes
python3 -m learning_engine --config $CFG --state $STATE dashboard --out /abs/path/coach-view.html     # read-only page, no server
```

`player.json` holds only: `player_ref` (a local alias, never a name), `age`, optional `goal_skill`, `minutes_available`, `equipment_available`, `skill_ratings`
(`{"close_control": {"rating": 2, "scale_max": 5, "rated_by": "NAME", "rated_on": "2026-10-01"}}`), `constraints` (`avoid_equipment`, `max_items`), `health_note` (the engine
only refers it to the coach). Unknown fields are refused. Fixture end-to-end demonstration (not a live search, not a real video analysis):
`python3 tests/demo_knowledge_cycle.py /absolute/NEW/empty/scratch/dir`.

Daily schedule: the templates in `deploy/learning_engine/` run `run`, which includes the pipeline pass when `pipeline.enabled` is true. They are not installed or enabled; a real
daily run needs a chosen host and your separate decision.

## First live search (public keyword metadata) - prepared, NOT yet run

Mode `public_keyword_search`: no channel needed (see README). The private run folder `/Users/karim/kemet-le-first-live/` (outside the repository, `0700`) holds
`config.first-live.json` (locked: offline, no network) and `config.first-live.armed.json` (live, YouTube only, 1 search + 1 details request, 1 page, 5 results,
`max_retries 0`, no AI, zero AI budget), plus an empty `state/`. The only thing missing is the API key.

1. **Create the key yourself** in your own Google Cloud console (enable "YouTube Data API v3", create an API key, restrict it to that API). Nothing was created for you.
2. **Give it to the process without showing it**, in the terminal you will run from (the prompt is silent and the key is not stored in the shell history):
   ```sh
   read -rs KEMET_LE_YOUTUBE_API_KEY && export KEMET_LE_YOUTUBE_API_KEY
   ```
   Never paste it into a chat, a config file or the repository. The engine reads no `.env`.
3. **Run once** (Python 3.10+; the engine uses only the standard library):
   ```sh
   cd /Users/karim/Downloads/kemetfc-learning-engine
   python3 -m learning_engine --config /Users/karim/kemet-le-first-live/config.first-live.armed.json --state /Users/karim/kemet-le-first-live/state run
   ```
   Do not repeat it after a failure without looking at the report first. The report is `/Users/karim/kemet-le-first-live/state/reports/YYYY-MM-DD-live.md`.
4. Review: `list --status pending`, then the coach steps above. A candidate found this way is never approved automatically.

Real-data model training stays locked (no trusted visible-child coverage store, no consents on record). Nothing found by this search enters any training dataset.

The scheduler templates in `deploy/learning_engine/` are not installed and not enabled.
