# PLAYER ASSESSMENT: RECORD VIDEO or UPLOAD VIDEO

One assessment, two video sources. After the coach picks the player, a source screen offers **RECORD VIDEO**
(the existing camera flow, unchanged) and **UPLOAD VIDEO** (Photo Library or Files). Both end as the same
`PendingAssessmentRecord` and go through the same upload code (`finalizeAndUpload`), so the future AI pipeline
sees one kind of evidence. The only difference is `videoSource`: `CAMERA_RECORDING` or `IMPORTED_VIDEO`.

## Flow of an imported video

1. The player was chosen first. Its id goes on the record; nothing is read from a file name or from the video.
2. The picker returns a file. It is copied (file-level copy, never into memory) to `Documents/ImportStaging`,
   then moved into `Documents/Recordings/<new id>.<original extension>`. No re-encoding, trimming or recompression.
   Photos is asked for the video as stored (`preferredItemEncoding: .current`), not a "compatible" re-encode.
3. On the stored copy: file size, SHA-256 (4 MiB chunks), duration, upright picture size (rotation applied), frame rate.
4. Imported-video integrity check: file exists and is non-empty, readable, playable, not copy-protected, has a video
   track, duration > 0, sane picture size, a frame rate, and within the 2 GB upload limit. The camera rule "the file
   must cover the time the assessment ran" is not used, because no assessment timer ran.
5. The record is written (durable, survives a restart) but is **not uploaded**: `coachSavedAt` is empty, so
   `isRetryable` is false and no automatic path (launch, foreground, network restored, Retry) can pick it up.
6. Review shows PLAYER, VIDEO SOURCE "Uploaded Video", DURATION, RESOLUTION, FILE SIZE, VIDEO STATUS READY or
   PROBLEM. SAVE ASSESSMENT is enabled only for READY. Cancel removes only this fresh copy and its record.
7. SAVE sets `coachSavedAt` and calls the existing `retryUpload`, which creates the tracking session and uploads
   (direct for <= 64 MB, multipart to storage above that) exactly as for a recording.
8. The screen says AI analysis "NOT AVAILABLE YET". No score, rating or progress for a pipeline that is not running.

## What the server learns without a migration

`location_id` = `kemetfc_ios_imported_video` and `capture_device` = `iPhone (KemetFCTracker) imported video`
(free-text fields; a camera recording keeps its old values). The tracking session reports no live-tracking
model versions for an imported video (they did not run). The video's SHA-256 is stored on the phone record; the
server computes its own checksum on upload.

## Target player in the video (not decided here)

Choosing the player in the roster says WHO the assessment belongs to. It does not say WHICH person in the video is
that player. Nothing in the app or in A1 assumes the largest person is the player: A1 labels its automatic choice
`dominant_track_offline`, `identity_verified_against_named_player: false`. A later step adds a coach "tap the
player in the video" selection (a normalized box at a time), stored next to the record and passed to the AI as the
selection hint; imported and recorded videos use the same hook.

## Known limits

* The pickers and the whole screen flow need a physical-iPhone test (this machine cannot drive them).
* A video edited in Photos, or "optimised" iCloud media, is delivered in the form Photos currently holds, which is
  the closest to the original the system picker gives.
* Unsaved imports left by a crash are listed in Pending Uploads as "NOT SAVED" and can be deleted there; they are
  never uploaded or deleted automatically (staging leftovers older than an hour are cleaned).
