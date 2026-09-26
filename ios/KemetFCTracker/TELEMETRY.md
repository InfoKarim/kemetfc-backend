# Tracking telemetry: durable, idempotent, acknowledged

Local-first: a sample or event is written to disk **before** it is uploaded, and
deleted locally **only after the backend proves it durable**.

```
recordSample/recordEvent -> TelemetryQueueStore (disk) -> TelemetryUploader (single flight)
   -> POST /tracking/sessions/{id}/samples|events -> explicit acknowledgment -> local delete
```

## Identity

| Record  | Identity                                  | Assigned                              |
|---------|-------------------------------------------|---------------------------------------|
| sample  | `session_id` + `sequence_number` (>= 1)   | once, by the store, persisted with it |
| event   | `session_id` + `client_event_id` (UUID)   | once, at enqueue                      |

Retries re-send the very same identities. The backend enforces
`UNIQUE(session_id, sequence_number)` and `UNIQUE(session_id, client_event_id)`
in the database (nullable columns: old rows and old clients keep working).

## Backend contract

* Samples `201`: `{"ingested", "duplicates", "acknowledged_sequences": [...]}` where the
  list is exactly the submitted sequences that are durably accounted for.
* A batch is **atomic** for identity conflicts. If any submitted sequence already exists
  with different content, the whole request is rolled back, nothing is acknowledged and the
  answer is `409` with `mismatched_sequences` and `conflicts[].differing_fields`.
* "Same content" = the client-authored fields (t_seconds, player/ball bbox, center,
  confidence, track id, pose_keypoints, gimbal/tracking mode/status) after schema
  defaults; server-generated fields and the identity itself are not compared.
* Events: `201` new, `200` identical retry (`duplicate: true`), `409` same id with different
  content. `occurred_at` stays the server receipt time; `client_occurred_at` is device time.
* Samples that arrive after `/complete` are accepted; when a request inserts at least one new
  row the cached feature set is invalidated. An exact duplicate retry does not invalidate it.

## Sample fields

See `TRACKING_SEMANTICS.md`: a field is null when the system does not know it (no stale box, no placeholder
ids, no unassociated pose). `identity_ambiguous` carries the tracker's own ambiguity flag to the backend quality gate.

## Client rules

* A sample is deleted only if the response lists it in `acknowledged_sequences`, the uploader
  actually sent it, and it belongs to the contiguous run starting at the ack cursor.
  2xx without that list, 409, 5xx, offline and undecodable bodies delete nothing.
* An event is deleted only when the response echoes its own `client_event_id`.
* One upload pass per session at a time (`TelemetryQueueStore.runFlight`).
* `TelemetryReplayService` replays anything pending after a restart, on foreground and when the
  network returns. `AssessmentSessionViewModel` only signals those moments.
* Storage: `Application Support/KemetFC/telemetry/<sessionId>/`, excluded from iCloud backup,
  file protection `completeUntilFirstUserAuthentication` on iOS. Paths and logs contain the
  session id and counts only.
* 150 MB per session is a **warning only** (log + `onStorageWarning`). Unacknowledged telemetry is
  never deleted for age or size; sessions pending over 30 days are only reported in the log.

## Known issues

* **A genuine, permanent 409 is retried on every ~2 s upload pass forever.** No data is discarded,
  but a permanent identity conflict should eventually move to a quarantined / human-review state
  instead of repeatedly hitting the backend. Not implemented yet.
* If a finished, fully acknowledged session id were reused, its sequence numbers would restart at
  1. The app never reuses session ids.
* The append path writes synchronously on the caller's thread (about 0.04 ms on macOS). Main-thread
  cost on a real device has not been measured.
