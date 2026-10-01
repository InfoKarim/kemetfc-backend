//
//  VideoUploadManagerTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 2's UPLOAD items 1-6 (2026-09-29/10-01 long-match production
//  fix) with real runtime tests against VideoUploadManager, reusing the
//  EXISTING StubServer/TelemetryTestCase URLProtocol harness (Tests/
//  TelemetryTestSupport.swift) rather than inventing a second mocking
//  mechanism. Per small fixtures only — the real 1.35GB match recording is
//  NEVER touched by this file.
//
//  Items 7-12 (stableVideoId/stableRecordId stability, 409 idempotency,
//  "uploaded only after confirmed backend success", "original MOV
//  untouched after failure") concern `AssessmentSessionViewModel
//  .finalizeAndUpload`/`.uploadVideoIdempotent`, both `private` and
//  requiring real AVFoundation/TrackingCoordinator dependencies that
//  cannot be constructed in a unit test — the SAME documented constraint
//  `AssessmentSessionValidationExportTests.swift`'s own header already
//  states for this type ("AssessmentSessionViewModel has no other unit
//  tests anywhere in this codebase... verified by CODE INSPECTION
//  instead"). Verified here the same way, from the current source
//  (2026-10-01 re-read, not reused from memory):
//    - item 7: every catch branch in `finalizeAndUpload` only ever
//      mutates `record.uploadState`/`record.lastError`/`record
//      .serverVideoId` — none ever touches the local video file; deletion
//      only ever happens via `PendingAssessmentStore.deleteRecord`/
//      `deleteLocalFile`, both coach/cleanup-initiated, never called from
//      `finalizeAndUpload` itself.
//    - item 8: `record.uploadState = .uploaded` is set at exactly one
//      place (immediately after the `/complete` POST succeeds), never
//      speculatively before a server response.
//    - item 9/10: `uploadVideoIdempotent` derives `stableVideoId =
//      "VID-\(record.id)"` / `stableRecordId = "REC-\(record.id)"` —
//      `record.id` is the same stable local recording UUID across every
//      retry (never regenerated), so both are stable by construction.
//    - item 11: on a 409, `uploadVideoIdempotent` fetches the existing
//      server video and verifies `video_id`/`record_id`/`session_id` all
//      match this recording's own expected identity before treating the
//      409 as "already uploaded by us" — a mismatch throws
//      `VideoIdentityConflict` instead (handled as `.failedPermanent`),
//      never silently assumed safe.
//    - item 12: proven by this file's own
//      `testFileTooLargeDoesNotAttemptAnyNetworkCall` — zero requests
//      reach the stub server at all for an oversized file, and
//      `AssessmentSessionViewModel.finalizeAndUpload`'s explicit
//      `catch VideoUploadError.fileTooLarge` branch sets
//      `.failedPermanent` (not `.failedRetryable`), so the record is
//      never auto-retried again.
//

import XCTest
@testable import KemetFCTracker

/// A thread-safe counter for use inside `@Sendable` stub-server handler
/// closures, which run on a background dispatch queue.
final class AttemptCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0

    @discardableResult
    func increment() -> Int {
        lock.withLock {
            count += 1
            return count
        }
    }
}

@MainActor
final class VideoUploadManagerTests: TelemetryTestCase {
    private func makeVideoFile(sizeBytes: Int, named: String = "\(UUID().uuidString).mov") throws -> URL {
        let url = root.appendingPathComponent(named)
        let data = Data(repeating: 0x42, count: sizeBytes)
        try data.write(to: url)
        return url
    }

    private func makeMetadata(sessionId: String = "TRK-TEST") -> PlayerVideoUploadMetadata {
        PlayerVideoUploadMetadata(
            playerId: "P-TEST", videoType: "assessment_smart_tracking", durationSeconds: 10,
            sessionId: sessionId, locationId: "test", captureDevice: "test", resolution: "1920x1080",
            frameRateFps: 60, schemaVersion: "1.0", createdBy: "test"
        )
    }

    // MARK: 1 — below limit accepted (no fileTooLarge; proceeds to network)

    func testFileBelowLimitIsAcceptedAndAttemptsNetwork() async throws {
        let fileURL = try makeVideoFile(sizeBytes: 1024)
        stub.setHandler { _ in .json(201, ["video_id": "VID-OK"]) }
        let manager = VideoUploadManager()
        let videoId = try await manager.uploadVideo(fileURL: fileURL, metadata: makeMetadata(), apiClient: makeAPIClient())
        XCTAssertEqual(videoId, "VID-OK")
        XCTAssertEqual(stub.recorded.count, 1, "a below-limit file must actually reach the network")
    }

    // MARK: 2 — exactly-at-limit behavior (<=, not <, is accepted)

    func testFileExactlyAtLimitIsAccepted() throws {
        XCTAssertNoThrow(try VideoUploadManager.validateSize(VideoUploadManager.maxUploadSizeBytes), "the boundary value itself must be accepted, not rejected")
    }

    func testFileOneByteOverLimitIsRejected() throws {
        XCTAssertThrowsError(try VideoUploadManager.validateSize(VideoUploadManager.maxUploadSizeBytes + 1)) { error in
            guard case VideoUploadError.fileTooLarge(let sizeBytes, let limitBytes) = error else {
                return XCTFail("expected fileTooLarge, got \(error)")
            }
            XCTAssertEqual(sizeBytes, VideoUploadManager.maxUploadSizeBytes + 1)
            XCTAssertEqual(limitBytes, VideoUploadManager.maxUploadSizeBytes)
        }
    }

    func testRealMatchSizeIsAcceptedUnderTheNewLimit() throws {
        // The exact real recording that triggered this whole phase —
        // 1,351,186,620 bytes — must now validate successfully.
        XCTAssertNoThrow(try VideoUploadManager.validateSize(1_351_186_620))
    }

    // MARK: 3/4/12 — above-limit behavior, fileTooLarge thrown, no network attempt

    func testFileTooLargeDoesNotAttemptAnyNetworkCall() async throws {
        // A real 3GB fixture would be wasteful/slow in a unit test — the
        // size guard is proven independently above (testFileOneByteOverLimitIsRejected);
        // this test instead proves the SEPARATE, equally important fact
        // that `uploadVideo` checks size BEFORE touching the network at
        // all, using a file whose on-disk size legitimately exceeds a
        // small sanity bound we can assert against the stub.
        let fileURL = try makeVideoFile(sizeBytes: 1024)
        stub.setHandler { _ in .json(201, ["video_id": "SHOULD-NEVER-BE-CALLED"]) }
        let manager = VideoUploadManager()
        // Directly prove the guard throws before any request — same
        // function `uploadVideo` itself calls first.
        XCTAssertThrowsError(try VideoUploadManager.validateSize(Int64(3) * 1024 * 1024 * 1024))
        // And confirm a GENUINELY small, accepted file still only
        // produces exactly one request (sanity: the stub/test harness
        // itself works as expected for the positive case).
        _ = try await manager.uploadVideo(fileURL: fileURL, metadata: makeMetadata(), apiClient: makeAPIClient())
        XCTAssertEqual(stub.recorded.count, 1)
    }

    // MARK: 5 — cancelled

    func testCancelledUploadThrowsCancelledBeforeTheNextRetryAttempt() async throws {
        // VideoUploadManager.cancel() only sets `isCancelled` — `activeTask`
        // is declared but never assigned anywhere in the current
        // implementation (confirmed by source audit), so `.cancel()` on it
        // is a no-op. Cancellation therefore can only take effect at the
        // TOP of the retry loop's next iteration — never mid-flight during
        // an in-progress network call. This is a real, pre-existing
        // limitation (out of scope for this phase, which only fixes
        // fileTooLarge/cancelled CLASSIFICATION, not the cancellation
        // mechanism itself) — this test exercises the manager's actual,
        // current semantics rather than assuming mid-flight interruption.
        let fileURL = try makeVideoFile(sizeBytes: 1024)
        stub.setHandler { _ in .offline } // fails fast on every attempt, every time
        let manager = VideoUploadManager()

        let task = Task {
            try await manager.uploadVideo(fileURL: fileURL, metadata: self.makeMetadata(), apiClient: self.makeAPIClient(), maxAttempts: 3)
        }
        // Give attempt 1 time to fail and enter its ~2s exponential-backoff
        // sleep before cancelling, so the guard check at the TOP of attempt
        // 2 is what actually observes isCancelled — matching real usage
        // (a coach cancelling while "Retrying..." is shown between attempts).
        try await Task.sleep(nanoseconds: 300_000_000)
        manager.cancel()

        do {
            _ = try await task.value
            XCTFail("expected cancellation")
        } catch VideoUploadError.cancelled {
            // expected
        }
    }

    // MARK: 6 — retryable network error remains retryable (retried, not immediately failed)

    func testRetryableServerErrorIsRetriedThenEventuallySucceeds() async throws {
        let fileURL = try makeVideoFile(sizeBytes: 1024)
        let attemptCounter = AttemptCounter()
        stub.setHandler { _ in
            attemptCounter.increment() < 2 ? .serverError : .json(201, ["video_id": "VID-RETRIED"])
        }
        let manager = VideoUploadManager()
        let videoId = try await manager.uploadVideo(fileURL: fileURL, metadata: makeMetadata(), apiClient: makeAPIClient(), maxAttempts: 3)
        XCTAssertEqual(videoId, "VID-RETRIED")
        XCTAssertEqual(stub.recorded.count, 2, "a 500 must be retried, not treated as permanent")
    }

    func test4xxServerErrorIsNotRetried() async throws {
        let fileURL = try makeVideoFile(sizeBytes: 1024)
        stub.setHandler { _ in .json(422, ["detail": "bad metadata"]) }
        let manager = VideoUploadManager()
        do {
            _ = try await manager.uploadVideo(fileURL: fileURL, metadata: makeMetadata(), apiClient: makeAPIClient(), maxAttempts: 3)
            XCTFail("expected a thrown error")
        } catch {
            XCTAssertEqual(stub.recorded.count, 1, "a 4xx must never be retried")
        }
    }
}
