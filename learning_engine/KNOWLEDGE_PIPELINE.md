# Knowledge pipeline (local) - what it is, what it is not

```
discovered -> awaiting_media | skipped -> ready -> processing -> draft_ready | skipped | failed
 YouTube metadata      needs a local file you may analyse        analyzer notes WITH video times -> PENDING draft -> coach review
                                                                  -> approved knowledge -> explained recommendation -> recorded results
```

* **Queue** (`work_queue`, persistent): one row per source, with attempts, reasons, lease and retry time. A skipped or failed source never stops
  the others; a crashed worker's lease expires and the attempt is counted; retries back off (60 s, 120 s, ... capped) and stop at `max_attempts`.
  Limits per pass: items, seconds, file size (`pipeline.*`). Nothing loops without an end.
* **Access to video.** The official pages (read 2026-10-08) say: no downloading, caching or storing copies of YouTube audiovisual content without
  YouTube's written approval (developer policies III.E.1), no accessing audiovisual content by any means other than the API (III.I.14), no scraping
  (III.E.6), no separating audio from video (III.I.7); the IFrame player API gives playback control and events, not frames, audio or transcripts. So
  **a YouTube candidate can only wait in `awaiting_media`**. The only analysable input is a local file with a written rights record (own recording,
  the owner's permission, or a licence). That permission is for analysis; `training_allowed` is fixed at 0 by the database. A copy of a YouTube video
  is not a rights basis. III.E.4.h also bars using API data to create "new or derived data or metrics", another reason not to build knowledge from
  YouTube metadata. The policy page does not mention model training; I did not find a statement either way and make no claim.
* **Analyzer.** No real analyzer is built in. `app/video_ai` in this repository measures body movement (pose, image-plane proxies), not drill steps,
  equipment or errors, so it is not wired in. With no analyzer the answer is `ANALYZER_NOT_CONFIGURED` and sources with media stay `ready`. A real one
  is plugged in by code through `learning_engine.analyzer.Analyzer` (never by a config string); external (cloud) analyzers are refused, and media
  with minors are never sent outside whatever else is configured.
* **Evidence.** A note is kept only with a valid video time range; steps and equipment of a draft come only from timed notes; age, duration, safety,
  objective, skills and measurement from an analyzer are flagged `unverified_fields` and a coach must confirm them before approval.
* **Library.** One permanent record per analysed file (`media_files`, `analyses`, `knowledge_notes`) tied to its drafts and decisions. "Knowledge
  extracted" means an analysis really produced timed notes; an analysis with nothing is recorded as `no_knowledge` and not counted. A re-analysis makes
  a NEW pending draft and never changes approved content. YouTube-API identifiers (id, link, title, channel) are not copied into the library: a linked file
  reads them from `sources`, which keeps the 30-day refresh/delete rule; after it the entry says the source was removed, while the analysis, the timed notes
  and the coach's decisions stay (they come from the local file and the coach).
* **Review.** Existing rules unchanged: pending until a coach decides, versions, invalidation, re-review. The reviewer id typed in the CLI is text, not
  authentication. Nothing is published to players.
* **Recommendations.** `recommend.py`: approved, current, rights-active, complete drills only; filters on age, time and equipment; target = the coach's
  stated goal, else the lowest *documented* rating (who, when, scale), else GENERIC (labelled). A health note produces nothing and refers to the coach or a
  specialist. Each recommendation shows the drill version, the reason, the source link and video times (if any), the measurement, what is missing, and waits
  for a coach. It is re-checked on every display and flagged `needs_rereview` if its drill stops being valid. No AI service, no model.
* **Results.** `outcomes.py`: executed or not, date, before/after with the method used for each, conditions, notes. Changes are shown as recorded
  results (not comparable if the method differs); a plain rule may suggest a coach looks at the plan again. Nothing is changed automatically and no model
  is trained from them.
* **Not in this build:** a real analyzer, any real video understanding, an external or paid analysis, training, a server or UI beyond `dashboard` (a
  read-only HTML file), an installed or enabled scheduler. Nothing here shows that a recommendation helps a player or reaches the level of any club's
  coaches; that needs independent evaluation.

Commands: `queue`, `media-register`, `rights-withdraw`, `pipeline-run`, `reanalyze`, `library`, `drafts`, `draft`, `recommend`, `rec-list`, `rec-show`,
`rec-decide`, `outcome-add`, `outcomes`, `dashboard` (see `RUNBOOK.md`). Enable the pass inside the daily run with `pipeline.enabled: true`.
