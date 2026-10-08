# KEMET AI Learning Engine V1 (research sidecar)

A local, **offline-by-default** tool that helps coaches find and review **Dribbling drill sources**. It is not a model, does
not learn, and nothing it stores ever enters a training dataset (`app/ml1` never imports it; a test checks that).

## What it does and does not do

| It does | It does not |
|---|---|
| Search approved YouTube channels (official Data API, API key, read-only) and record that a public video exists: video id, channel id, link, title, publish date, duration, when found and when verified, and which query version found it | Scrape, download video or audio, fetch captions or transcripts, or read comments |
| Mark each candidate `discovered` -> `metadata_only` (or excluded: not public, made for kids, live, implausible length, channel changed, or not returned = unavailable) | Treat a title as proof of the drill's steps or of suitability for any age |
| Keep optional **general** AI drafts, disabled by default, from one fixed child-free prompt | Send videos, images, pose data, names, ids, ratings, source titles or any discovered metadata to an AI service |
| Let a coach write the drill content (steps, equipment, duration, age range, safety notes) and decide `pending -> approved/rejected` with reasons, version and hash | Approve anything automatically, publish to players, or change any programme (`published` is fixed at 0 by the database) |
| Keep its own private database, a hash-chained audit log, daily JSON + Markdown reports | Touch the application database, uploads, players, consents, camera, tracking, telemetry, DockKit or iOS |

States shown to the coach: **discovered**, **metadata-only**, **AI draft**, **coach-reviewed**. A coach-reviewed item is
**approved** or **rejected**; everything else is **pending**.

## Layout

`config.py` strict configuration and the gates (`youtube_blockers`, `paid_blockers`) - `store.py` private SQLite, locks, audit -
`http.py` the only network code (https, host allowlist, no redirects, size/time caps, no secrets in errors) - `youtube.py` search.list
and videos.list client - `discovery.py` validation, duplicates, states - `budget.py` quota and cost reservations, pacing -
`ai.py` OpenAI/Anthropic adapters (disabled) - `review.py` coach review and source validity - `retention.py` 30-day rule - `daily.py` the run and report -
`cli.py` command line. Optional backend review API: `app/routers/learning_review.py` (locked by default). Scheduler templates:
`deploy/learning_engine/` (not installed).

## Using it (offline first)

```sh
python3 -m learning_engine --config learning_engine/config.example.json --state /absolute/private/dir validate-config
python3 -m learning_engine --config learning_engine/config.example.json --state /absolute/private/dir run     # offline: no network
python3 -m learning_engine --config ... --state ... list --status pending
python3 -m learning_engine --config ... --state ... save-content ITEM --file drill.json --reviewer-id NAME
python3 -m learning_engine --config ... --state ... decide ITEM --version N --decision approved --reviewer-id NAME --reasons "..." --source-checked LINK
python3 -m learning_engine --config ... --state ... export-approved       # read-only; a coach carries content into the drill library by hand
```

`config.example.json` is locked: offline, no network, no channels, no providers, zero budget, no secrets. Copy it to a private place
and edit the copy. Keys are never in the config: only the NAME of an environment variable (`KEMET_LE_YOUTUBE_API_KEY` etc.). No
`.env` is read.

### Going live is a human decision with every condition written down

YouTube: `mode: "live"`, `network_enabled: true`, `youtube.enabled: true`, at least one allowlisted channel (each with the basis for
allowing it, who reviewed it and when - none is supplied or presumed), and the key in the named environment variable. Paid AI: all of
that plus, per provider, `enabled`, the model name YOU choose (none is built in), YOUR price with the page and date it came from,
token and per-call cost ceilings, the daily budget, and `paid_execution_approved_by/on`. Any missing item blocks the call and is listed
in the report. **Nothing in this build ran live or paid.**

### Reviewing

* Backend API (real accounts): set `LEARNING_ENGINE_REVIEW_API_ENABLED=1` and `LEARNING_ENGINE_STATE_DIR=/absolute/path`;
  coach or admin only; the reviewer is the logged-in account (`auth_mode: backend_user`). Otherwise every route is 404.
* Command line: the reviewer id is **typed text, not authentication** (`auth_mode: local_cli_unauthenticated`, a notice is printed).
  It is fine for one person on one machine; it is not a multi-user system.
* Changing approved content creates a new version and returns the item to pending. An approval records reviewer, time, decision,
  reasons, content version and hash, and the source link(s) the coach says they inspected.
* **A source can go bad after approval** (private, deleted or not returned, made for kids, other channel, live, implausible length).
  Approved sources are re-checked on every live run. The earlier decision, the coach's content and the history are kept, but the item
  gets `approval_state: review_required` with the reason and the time, is **left out of `export-approved`**, and shows the current
  source status next to the old decision. A source that turns public again does **not** restore the approval: a coach or admin has to
  decide again on the **current version**, while the source is confirmed available, recording the link they looked at (a rejection is
  always possible). `list --needs-source-review`, `GET /learning-review/items?needs_source_review=true`.
* **Two separate facts**: `approval_state` (`current` / `review_required`: is the coach's approval still valid) and `source_state`
  (`present` / `removed`: does the engine still hold the source data). Retention changes only the second. An approval that was suspended
  stays suspended after the source is deleted, with its reason and date, until a person decides again. That decision, for content whose
  source is gone, is recorded as `review_basis: INDEPENDENT_CONTENT_SOURCE_REMOVED` and shown as an independent review of the coach's own
  content, not a check of the deleted source. `valid_for_export` is on every item the API and CLI show. An item whose source expired
  but was **never** invalidated keeps its current approval and is exported flagged `source_removed` (retention alone is not evidence the
  content went bad; this is a documented choice, not a verification).

## Limits (cost, quota, rate)

* YouTube request counts and quota units are tracked separately and persisted: `search_calls_per_day` (documented default bucket 100,
  each call 1) and `units_per_day` (documented default 10,000 for other endpoints). The quota day is the Pacific-time day.
* AI: per-call token and cost ceilings, daily requests/tokens/cost caps (hard maximum $5/day in code), reserved before sending.
  **Every request that was sent counts against the request cap, whatever happened to it afterwards.** The ledger row is committed as
  `sent` before the transport is called, then becomes `settled` (usage reported), `rate_limited` (HTTP 429) or `uncertain` (HTTP
  5xx/504, timeout, lost connection, unreadable answer, no usage numbers). Rate-limited and uncertain rows keep their worst-case
  token and cost reservation. A paid request that ended uncertain is **never retried automatically**, and the daily run does not send
  a second one that day. The only automatic retry is a 429 that carries a `Retry-After` of at most `max_backoff_s`, at most
  `max_retries` (0-2) times, each retry a new reservation that counts against every cap. A reservation is released (and audited) only
  for a request proven never sent (refused by the local https/host checks before any connection). The report shows, per provider,
  `requests_attempted`, `requests_sent`, `requests_counted_against_cap`, `settled`, `uncertain`, `rate_limited`, `in_flight`,
  `released_not_sent`. Costs are reserved and estimated from operator-supplied prices: **not a guarantee of an invoice**.
* Pacing between external requests (`min_interval_s`, kept across restarts, per provider for AI), timeouts, bounded backoff.
  The retry rules follow the providers' error pages (read 2026-10-08): neither says whether a failed or refused request is billed or
  processed, so the safe reading is applied. Quota accounting for YouTube was not changed.

## Retention (YouTube API data)

A refresh (videos.list) replaces the stored title, publication date, duration, privacy, made-for-kids flag and text fingerprint from
the CURRENT response, and moves `verified_at` only when the response was complete and valid. A partial or malformed response changes
nothing and renews nothing. If two reviewed sources end up with the same normalized title, both rows and all their items stay and the
clash is recorded; a duplicate follows the canonical it now matches or becomes its own candidate.

After 30 days without a good refresh **everything the API gave** is deleted: title, dates, duration, fingerprint and also the video
id, channel id and link (I could not confirm that the policy allows keeping a bare id or link, so the safe reading is applied). A
coach's own content and decision history are kept, flagged `source_state: removed` ("the source was removed after the retention
window") with any suspension of the approval untouched, the recorded source links in the decision history are replaced by a marker, and a bare candidate with no coach content is
deleted. Item ids made since version 2 are random and contain no video id. This is not a legal-compliance statement. See
`retention.py` for what is not removed (audit entries from version 1, links a coach pasted into their own notes).

## Official sources used (read 2026-10-08; the fetch tool summarises pages, so re-check before relying on any figure)

* YouTube quota: https://developers.google.com/youtube/v3/determine_quota_cost (page stamped "Last updated 2026-09-15 UTC")
* YouTube API Services Developer Policies, III.E.4 (30-day refresh/delete): https://developers.google.com/youtube/terms/developer-policies
* search.list: https://developers.google.com/youtube/v3/docs/search/list - videos.list: https://developers.google.com/youtube/v3/docs/videos/list -
  video properties: https://developers.google.com/youtube/v3/docs/videos
* API key header guidance: https://docs.cloud.google.com/docs/authentication/api-keys-use (header recommended; I cannot confirm that YouTube Data API v3
  accepts the header form, so `youtube.api_key_transport` can be set to `query`)
* Anthropic: https://platform.claude.com/docs/en/api/overview and https://platform.claude.com/docs/en/api/messages
* OpenAI: https://developers.openai.com/api/reference/overview and https://developers.openai.com/api/reference/resources/responses/methods/create
  (usage field names in the response could not be confirmed from the reference page; a reply without recognised usage is treated as uncertain)
* Prices: not looked up and not built in. Provider pricing pages must be checked by the operator at enable time.

## Known gaps

Database schema is version 3 (an existing version-1 or version-2 file is upgraded in place in one transaction; every version-1 AI row that might have been sent is counted as uncertain; a version-2 item whose suspension cannot be shown from the audit log becomes `review_required`, never valid). No real channel allowlist exists; no live YouTube or paid-provider call has ever been made; the search/videos response handling is tested
only against fakes written from the documentation; systemd templates untested; the review API is not exposed in any UI; coach identity
via CLI is unauthenticated; duplicate detection is exact-text only; approved drills are not connected to the drill library on purpose.
