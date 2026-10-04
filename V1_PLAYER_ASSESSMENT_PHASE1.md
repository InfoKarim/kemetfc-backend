# Player Assessment — Phase 1

Phase 1 of the unified Player Assessment: one workflow, **PLAYER ASSESSMENT**, for a coach to pick a
player, start or continue an assessment, see all eight stations, and enter **official manual times** for
three of them. Recording, video and AI are **not** part of Phase 1.

```
Coach signs in → Player Assessment (/v1/sprint) → Select player
  → START NEW ASSESSMENT  (or CONTINUE ASSESSMENT when one is in progress)
  → Assessment overview: eight stations → open Sprint, Agility or Dribbling → add attempts
```

## What is open

| # | Station | Phase 1 |
|---|---|---|
| 1 | Sprint (S1) | official manual time, 3 valid attempts within 5 |
| 2 | Agility (S2) | official manual time **per turning foot** (left, right), 2 valid within 4 for each foot |
| 3 | Ball Mastery (S3) | COMING SOON — takes nothing |
| 4 | Dribbling (S4) | official manual time, 3 valid within 5 |
| 5 | Passing / Receiving (S5) | COMING SOON |
| 6 | Shooting (S6) | COMING SOON |
| 7 | 1v1 (S7) | COMING SOON |
| 8 | Small-Sided Game (S8) | COMING SOON |

Every rule comes from the frozen standard (`V1.0-RC2`, section 4.1 and 7): the numbers above, the
invalid reasons for each station, **a missed mandatory cone is INVALID and repeatable (`MISSED_CONE`)**,
and **a cone contact alone is not an invalid reason**. The official value is the typed stopwatch reading,
stored as exact hundredths (`4.25` shows again as `4.25`, `4.20` as `4.20`), source `HUMAN_MANUAL`.
The counts (valid of required, attempt limits) only describe progress; they never block an entry.

The assessment stays **IN PROGRESS**. Five stations are not available, and nothing can complete an
assessment or waive a station, so Phase 1 never marks the eight-station assessment COMPLETE.

## API added

* `GET /v1/assessments/{id}/stations` — all eight stations, a derived state (`NOT_STARTED`,
  `IN_PROGRESS`, `COMPLETE`, `COMING_SOON`) and counts. Nothing is stored for the state.
* `POST /v1/assessments/{id}/attempts` — one whole attempt in one transaction: the station run (on first
  use), the trial, and the time or its NOT_RECORDED reason. `client_entry_id` makes a repeat return the
  saved attempt (200). An attempt number that already holds different content is a **409**: nothing is
  overwritten.
* `POST /v1/station-runs/{id}/trials` now takes `series_key` (`L` or `R` for Agility).
* `GET /v1/outbox.js` — the offline-queue script.

No table, migration or index is added. `d4b7e9a1c635` is unchanged. Feature flag and allowlist are
unchanged (`V1_ASSESSMENT_ENABLED` OFF by default; empty allowlist admits nobody).

## The offline queue (unsynced manual attempts)

**Why.** A coach on a pitch can lose the connection for a moment. A typed, saved attempt must not vanish.

**How.** Every attempt is first written to the device, then sent. It leaves the device only when the
server confirms it. The queue is separate from the video `PendingAssessmentStore` and shares no code or
file with it.

**Exactly what is stored** — in the browser's `localStorage` for this web address, under the single key
`kemet_v1_outbox_v1` (on the iPhone app this is the web view's own website storage):

* a random operation id (also the idempotency key sent as `client_entry_id`)
* the signed-in user's opaque id
* the assessment id
* the attempt: station id, series key (turning foot), attempt number, valid or not valid, the not-valid
  reason, and the typed time as **text** (for example `"4.25"`) or the time-not-recorded reason
* a state, timestamps, a retry counter and a short error code

**Never stored:** cookies, session or CSRF tokens, passwords, player names, video, telemetry, any media.
Anything else is refused by the queue itself.

**States:** `LOCAL_PENDING` (saved on the device, not confirmed), `SYNCING`, `SYNCED`, `SYNC_FAILED` (the
server definitively refused it). A network failure or timeout is never `SYNC_FAILED`: the entry stays
`LOCAL_PENDING` and is retried — when the page opens, when the connection returns, every 20 seconds
while the page is open, and from the SEND NOW button. Every retry carries the same operation id, so it
cannot be saved twice; a lost reply is answered by the server with the attempt it already saved.

**When it is removed**
* After a successful sync the typed content is deleted immediately. A small receipt (operation id, state,
  server ids, time) stays for **24 hours** so a repeat is recognised, then it is purged.
* An unsent entry is **never** deleted automatically, not even when it is old (after 14 days it is only
  flagged).
* A `SYNC_FAILED` entry is removed only by an explicit, two-tap DISCARD by the same user. It can also be
  retried with TRY AGAIN.

**Sign-out.** Signing out of the app does not clear web storage. Entries are tagged with the user, so
another coach signing in on the same device never sees, sends or discards them; they wait for the original
coach. Before handing a device to someone else, open Player Assessment while online so they are sent.

**Deleting a child's data.** The device holds no name, only an assessment id and the typed attempt, and only
until it is sent (normally seconds). If the server has since deleted the assessment or withdrawn consent,
the attempt is refused (`SYNC_FAILED`) and can be discarded.

**Consent.** Unchanged and checked by the server on every save, including a queued retry. A player without
the required consent is blocked, and the attempt is never accepted.

## Limits (known, deliberate)
* The page cannot be opened without a connection; the queue protects attempts typed while the connection
  drops, and sends them next time the page is opened online.
* Starting a new assessment, opening a station list and **corrections** need the connection (a correction
  needs the server's current value and a second person).
* Entry-time second confirmation (standard 6.2) and plausibility warnings (windows are only
  `KEMET-PROPOSED`) are not enforced, as in S1.
* No camera, recording, video, telemetry or upload control exists on this screen.
