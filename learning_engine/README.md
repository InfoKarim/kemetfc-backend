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
`ai.py` OpenAI/Anthropic adapters (disabled) - `review.py` coach review - `retention.py` 30-day rule - `daily.py` the run and report -
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

## Limits (cost, quota, rate)

* YouTube request counts and quota units are tracked separately and persisted: `search_calls_per_day` (documented default bucket 100,
  each call 1) and `units_per_day` (documented default 10,000 for other endpoints). The quota day is the Pacific-time day.
* AI: per-call token and cost ceilings, daily requests/tokens/cost caps (hard maximum $5/day in code), reserved before sending,
  settled with real usage when the provider reports it, otherwise **uncertain** (stays counted, never retried). Retries are bounded and each attempt is
  a new reservation. Costs are reserved and estimated from operator-supplied prices: **not a guarantee of an invoice**.
* Pacing between external requests (minimum interval kept across restarts), timeouts, bounded retries with backoff.

## Retention (YouTube API data)

Stored API data is refreshed or deleted within 30 days (live runs re-verify sources nearing the limit; others are deleted; a coach's
approved content stays but its API-derived fields are cleared). See `retention.py` for exactly what is kept and the one point I
cannot confirm (the bare video id and link after 30 days).

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

No real channel allowlist exists; no live YouTube or paid-provider call has ever been made; the search/videos response handling is tested
only against fakes written from the documentation; systemd templates untested; the review API is not exposed in any UI; coach identity
via CLI is unauthenticated; duplicate detection is exact-text only; approved drills are not connected to the drill library on purpose.
