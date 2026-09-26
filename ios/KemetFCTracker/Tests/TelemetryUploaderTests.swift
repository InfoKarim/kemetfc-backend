//
//  TelemetryUploaderTests.swift
//  KemetFCTrackerTests
//
//  STATUS: written BEFORE the implementation (tests-first); compiles only
//  once the interface below exists. See TelemetryQueueStoreTests.swift for
//  the TelemetryQueueStore interface.
//
//  TARGET INTERFACE CHANGES to TelemetryUploader (recordSample/recordEvent
//  keep their exact current signatures, so TrackingCoordinator is untouched):
//      init(apiClient: KemetAPIClient, sessionId: String, store: TelemetryQueueStore)
//      func waitForPendingWrites() async   // returns once every record* call made so
//                                          // before it is durably on disk (a no-op if
//                                          // the implementation writes synchronously)
//      func flushPending() async           // one upload pass; concurrent calls join a
//                                          // single active flight
//      func stop()                         // unchanged signature; must not lose the tail
//      func stopAndFlush() async           // stop() + one final best-effort flush
//
//  ACKNOWLEDGMENT CONTRACT the uploader must honor (see the backend tests):
//    samples: 201 {"acknowledged_sequences": [...]} lists exactly the submitted
//             sequences that are durably accounted for. The uploader deletes
//             ONLY those (intersected with what it actually sent), and only
//             as a contiguous run from its cursor (TelemetryQueueStore rule).
//             Any 2xx without that list (missing/null/garbage/old-style
//             `accepted_through_sequence`) proves nothing.
//             409 (identity mismatch — the server committed NOTHING) and any
//             other failure keep every sample local; no cursor movement.
//    events : 201/200 echoing the same "client_event_id" -> delete that event.
//             Anything else (no echo, 409, 5xx, offline) keeps it.
//    stop() : cancels the timer and marks the session finished; it does no
//             network work (deterministic). `stopAndFlush()` = stop() + one
//             final flush pass, used by the app when a recording ends.
//
import XCTest
@testable import KemetFCTracker

final class TelemetryUploaderTests: TelemetryTestCase {
    private let session = "TRK_0001"

    private func makeStore() -> TelemetryQueueStore { TelemetryQueueStore(rootDirectory: root) }

    private func makeUploader(store: TelemetryQueueStore? = nil) -> (TelemetryUploader, TelemetryQueueStore) {
        let store = store ?? makeStore()
        return (TelemetryUploader(apiClient: makeAPIClient(), sessionId: session, store: store), store)
    }

    private func record(_ uploader: TelemetryUploader, count: Int, startingAt first: Int = 0) {
        for i in first..<(first + count) {
            uploader.recordSample(
                tSeconds: Double(i),
                playerBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
                playerConfidence: 0.9, playerTrackId: 1,
                ballBox: nil, ballConfidence: nil,
                poseKeypoints: [], gimbalState: "tracking_active", trackingMode: "smart_soccer", trackingStatus: "locked"
            )
        }
    }

    private func pendingSequences(_ store: TelemetryQueueStore) throws -> [Int?] {
        try makeStore().pendingSamples(sessionId: session, limit: 100_000).map(\.sequenceNumber)
    }

    private var sampleUploads: [RecordedRequest] { stub.recorded.filter(\.isSamplesUpload) }
    private var eventUploads: [RecordedRequest] { stub.recorded.filter(\.isEventUpload) }

    /// Answers samples with an explicit acknowledgment of the highest sequence in the request.
    private func acknowledgeEverything() {
        stub.setHandler { request in
            if request.isSamplesUpload {
                let sequences = request.sampleSequenceNumbers
                return .acknowledged(sequences: sequences, ingested: sequences.count)
            }
            let echoed = request.json["client_event_id"] as? String ?? ""
            return .json(201, ["event_id": "TEV_1", "client_event_id": echoed, "duplicate": false])
        }
    }

    // MARK: durability

    func testRecordedSamplesAreDurablyQueuedBeforeAnyUpload() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .offline }

        record(uploader, count: 5)
        await uploader.waitForPendingWrites()

        XCTAssertEqual(try pendingSequences(store), [1, 2, 3, 4, 5])
        XCTAssertTrue(stub.recorded.isEmpty, "nothing has been uploaded yet — this is purely local durability")
    }

    func testFailedUploadPreservesEveryQueuedSample() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .offline }
        record(uploader, count: 5)
        await uploader.waitForPendingWrites()

        await uploader.flushPending()

        XCTAssertFalse(sampleUploads.isEmpty, "an upload was attempted")
        XCTAssertEqual(try pendingSequences(store), [1, 2, 3, 4, 5])

        stub.setHandler { _ in .serverError }
        await uploader.flushPending()
        XCTAssertEqual(try pendingSequences(store), [1, 2, 3, 4, 5], "a 5xx keeps the data too")
    }

    func testUploaderRecreationRestoresPendingSamplesAndDeliversThem() async throws {
        do {
            let (firstProcess, _) = makeUploader()
            stub.setHandler { _ in .offline }
            record(firstProcess, count: 5)
            await firstProcess.waitForPendingWrites()
            await firstProcess.flushPending()
        }   // the "process" dies here: no in-memory state survives

        stub.reset()
        acknowledgeEverything()
        let (secondProcess, secondStore) = makeUploader()   // new store instance, same directory
        XCTAssertEqual(try pendingSequences(secondStore), [1, 2, 3, 4, 5], "restored from disk")

        await secondProcess.flushPending()

        XCTAssertEqual(sampleUploads.flatMap(\.sampleSequenceNumbers), [1, 2, 3, 4, 5])
        XCTAssertEqual(try pendingSequences(secondStore), [])
    }

    // MARK: acknowledgment semantics

    func testExplicitBackendAcknowledgmentAdvancesTheCursorAndPersistsIt() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .acknowledged(sequences: [1, 2, 3, 4, 5], ingested: 5) }
        record(uploader, count: 5)
        await uploader.waitForPendingWrites()

        await uploader.flushPending()

        XCTAssertEqual(try pendingSequences(store), [])
        XCTAssertEqual(try makeStore().pendingSamples(sessionId: session, limit: 10).count, 0, "the cursor is on disk")
    }

    func testHTTP2xxWithoutSufficientAcknowledgmentDoesNotDeleteLocalData() async throws {
        let insufficientReplies: [(String, StubReply)] = [
            ("legacy body without any acknowledgment", .json(201, ["ingested": 5])),
            ("empty object", .json(201, [:])),
            ("null acknowledgment", .json(201, ["ingested": 5, "duplicates": 0, "acknowledged_sequences": NSNull()])),
            ("empty acknowledgment", .json(201, ["ingested": 0, "acknowledged_sequences": []])),
            ("old-style accepted_through_sequence (never trusted)", .json(201, ["ingested": 5, "accepted_through_sequence": 5])),
            ("sequences that were never submitted", .json(201, ["ingested": 0, "acknowledged_sequences": [99, 100]])),
            ("a run that does not start at the cursor", .json(201, ["ingested": 5, "acknowledged_sequences": [2, 3, 4]])),
            ("not JSON", StubReply(status: 201, body: Data("OK".utf8))),
        ]
        for (label, reply) in insufficientReplies {
            StubServer.state.reset()
            let directory = root.appendingPathComponent(UUID().uuidString)
            let store = TelemetryQueueStore(rootDirectory: directory)
            let uploader = TelemetryUploader(apiClient: makeAPIClient(), sessionId: session, store: store)
            stub.setHandler { _ in reply }
            record(uploader, count: 5)
            await uploader.waitForPendingWrites()

            await uploader.flushPending()

            XCTAssertEqual(
                try store.pendingSamples(sessionId: session, limit: 100).map(\.sequenceNumber), [1, 2, 3, 4, 5],
                "2xx with \(label) is not positive evidence of durability"
            )
        }
    }

    func testPartialAcknowledgmentKeepsUnacknowledgedRecordsAndResendsOnlyThem() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .acknowledged(sequences: [1, 2, 3], ingested: 3) }
        record(uploader, count: 5)
        await uploader.waitForPendingWrites()

        await uploader.flushPending()
        XCTAssertEqual(try pendingSequences(store), [4, 5])

        acknowledgeEverything()
        await uploader.flushPending()

        XCTAssertEqual(sampleUploads.last?.sampleSequenceNumbers, [4, 5], "acknowledged samples are never sent again")
        XCTAssertEqual(try pendingSequences(store), [])
    }

    func testConflictResponseKeepsEveryLocalSampleAndMovesNoCursor() async throws {
        let (uploader, store) = makeUploader()
        // 409: the server rolled the whole batch back and acknowledges nothing.
        stub.setHandler { _ in
            .json(409, ["detail": "mismatch", "mismatched_sequences": [3],
                        "conflicts": [["sequence_number": 3, "differing_fields": ["t_seconds"]]]])
        }
        record(uploader, count: 5)
        await uploader.waitForPendingWrites()

        await uploader.flushPending()

        XCTAssertEqual(try pendingSequences(store), [1, 2, 3, 4, 5], "a 409 must not advance or delete anything")
        XCTAssertEqual(sampleUploads.count, 1, "a permanent conflict must not be hammered within one pass")
    }

    func testOnlySequencesTheUploaderActuallySentCanBeDeleted() async throws {
        let (uploader, store) = makeUploader()
        // The server "acknowledges" 1-5 plus 6 and 99, which this batch never contained.
        stub.setHandler { _ in .acknowledged(sequences: [1, 2, 3, 4, 5, 6, 99], ingested: 5) }
        record(uploader, count: 5)
        await uploader.waitForPendingWrites()

        await uploader.flushPending()
        record(uploader, count: 1, startingAt: 5)                 // sequence 6 arrives after the response
        await uploader.waitForPendingWrites()

        XCTAssertEqual(try pendingSequences(store), [6], "an acknowledgment cannot pre-delete data it was never sent")
    }

    // MARK: back-off (protects a server that never acknowledges)

    func testTheTimerBacksOffWhileUploadsAreNotAcknowledgedAndRecoversAfterAnAcknowledgment() async throws {
        let (uploader, _) = makeUploader()
        record(uploader, count: 3)
        XCTAssertEqual(uploader.currentFlushIntervalSeconds, 2.0, accuracy: 1e-9)

        // An old backend: 201 but no acknowledged_sequences.
        stub.setHandler { _ in .json(201, ["ingested": 3]) }
        var previous = uploader.currentFlushIntervalSeconds
        for _ in 0..<3 {
            await uploader.flushPending()
            XCTAssertGreaterThan(uploader.currentFlushIntervalSeconds, previous, "each unacknowledged pass doubles the wait")
            previous = uploader.currentFlushIntervalSeconds
        }
        for _ in 0..<20 { await uploader.flushPending() }
        XCTAssertLessThanOrEqual(uploader.currentFlushIntervalSeconds, 128.0, "bounded")

        acknowledgeEverything()
        await uploader.flushPending()
        XCTAssertEqual(uploader.currentFlushIntervalSeconds, 2.0, accuracy: 1e-9, "a real acknowledgment resets it")
    }

    func testExplicitFlushesAreNeverDelayedByTheBackOff() async throws {
        let (uploader, _) = makeUploader()
        record(uploader, count: 2)
        stub.setHandler { _ in .offline }
        for _ in 0..<5 { await uploader.flushPending() }
        let attemptsWhileBackedOff = sampleUploads.count
        acknowledgeEverything()
        await uploader.flushPending()
        XCTAssertEqual(attemptsWhileBackedOff, 5, "flushPending always attempts an upload")
        XCTAssertEqual(sampleUploads.count, 6)
    }

    // MARK: telemetry semantics reach the durable queue

    func testBallTrackIdAndIdentityAmbiguityArePersistedAsGiven_neverAPlaceholder() async throws {
        let (uploader, store) = makeUploader()
        uploader.recordSample(
            tSeconds: 1, playerBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4), playerConfidence: 0.9, playerTrackId: 1,
            ballBox: NormalizedRect(x: 0.6, y: 0.6, width: 0.03, height: 0.03), ballConfidence: 0.35, ballTrackId: 7,
            poseKeypoints: [], gimbalState: "tracking_active", trackingMode: "smart_soccer", trackingStatus: "locked", identityAmbiguous: true
        )
        uploader.recordSample(   // ball not detected: unknown, not 1
            tSeconds: 2, playerBox: nil, playerConfidence: nil, playerTrackId: nil, ballBox: nil, ballConfidence: nil,
            poseKeypoints: [], gimbalState: "tracking_active", trackingMode: "smart_soccer", trackingStatus: "temporarily_lost", identityAmbiguous: false
        )
        await uploader.waitForPendingWrites()

        let stored = try makeStore().pendingSamples(sessionId: session, limit: 10)
        XCTAssertEqual(stored[0].ballTrackId, 7)
        XCTAssertEqual(stored[0].identityAmbiguous, true)
        XCTAssertNil(stored[1].ballTrackId)
        XCTAssertNil(stored[1].playerBbox)
        XCTAssertNil(stored[1].playerTrackId)
        XCTAssertEqual(stored[1].trackingStatus, "temporarily_lost")
        _ = store
    }

    func testSampleSequenceFollowsCaptureOrder() async throws {
        let (uploader, _) = makeUploader()
        record(uploader, count: 10)
        await uploader.waitForPendingWrites()

        let stored = try makeStore().pendingSamples(sessionId: session, limit: 100)
        XCTAssertEqual(stored.map(\.sequenceNumber), (1...10).map { Optional($0) })
        XCTAssertEqual(stored.map(\.tSeconds), (0..<10).map { Double($0) }, "timestamps keep capture order")
    }

    // MARK: retry identity

    func testRetrySendsIdenticalSequenceIdentifiers() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .offline }
        record(uploader, count: 5)
        await uploader.waitForPendingWrites()
        await uploader.flushPending()
        let firstAttempt = try XCTUnwrap(sampleUploads.first).sampleSequenceNumbers
        XCTAssertEqual(firstAttempt, [1, 2, 3, 4, 5])

        record(uploader, count: 2, startingAt: 5)          // more data arrives while offline
        await uploader.waitForPendingWrites()
        acknowledgeEverything()
        await uploader.flushPending()

        let retry = try XCTUnwrap(sampleUploads.last).sampleSequenceNumbers
        XCTAssertEqual(Array(retry.prefix(5)), firstAttempt, "the retry re-sends the very same identities")
        XCTAssertEqual(retry, [1, 2, 3, 4, 5, 6, 7], "new samples continue the sequence; nothing is renumbered")
        XCTAssertEqual(try pendingSequences(store), [])
    }

    func testRepeatedRetriesNeverMintNewLocalIdentities() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .offline }
        record(uploader, count: 4)
        await uploader.waitForPendingWrites()

        for _ in 0..<5 { await uploader.flushPending() }

        XCTAssertTrue(sampleUploads.allSatisfy { $0.sampleSequenceNumbers == [1, 2, 3, 4] })
        XCTAssertEqual(try pendingSequences(store), [1, 2, 3, 4])
        XCTAssertEqual(try makeStore().pendingSamples(sessionId: session, limit: 100).count, 4, "no duplicate local records either")
    }

    // MARK: events

    func testEventRetryPreservesTheSameClientEventIdAndTimestamp() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .offline }
        uploader.recordEvent(type: "player_locked", details: ["target_track_id": .int(1)])
        await uploader.waitForPendingWrites()
        await uploader.flushPending()
        let firstAttempt = try XCTUnwrap(eventUploads.first).json
        let eventId = try XCTUnwrap(firstAttempt["client_event_id"] as? String)
        XCTAssertNotNil(UUID(uuidString: eventId))
        XCTAssertNotNil(firstAttempt["client_occurred_at"] as? String)

        acknowledgeEverything()
        await uploader.flushPending()

        let retry = try XCTUnwrap(eventUploads.last).json
        XCTAssertEqual(retry["client_event_id"] as? String, eventId)
        XCTAssertEqual(retry["client_occurred_at"] as? String, firstAttempt["client_occurred_at"] as? String)
        XCTAssertEqual(try makeStore().pendingEvents(sessionId: session), [], "acknowledged by the echoed client_event_id")
        _ = store
    }

    func testEventWithoutAnEchoedClientEventIdIsNotAnAcknowledgment() async throws {
        let replies: [StubReply] = [
            .json(201, ["event_id": "TEV_1"]),                                          // 2xx, no echo
            .json(201, ["event_id": "TEV_1", "client_event_id": "some-other-id"]),      // echo of a different id
            .json(409, ["detail": "mismatch"]),                                          // content conflict
            .serverError,
            .offline,
        ]
        for reply in replies {
            StubServer.state.reset()
            let store = TelemetryQueueStore(rootDirectory: root.appendingPathComponent(UUID().uuidString))
            let uploader = TelemetryUploader(apiClient: makeAPIClient(), sessionId: session, store: store)
            stub.setHandler { _ in reply }
            uploader.recordEvent(type: "player_lost", details: [:])
            await uploader.waitForPendingWrites()

            await uploader.flushPending()

            XCTAssertEqual(try store.pendingEvents(sessionId: session).count, 1, "status \(reply.status) must keep the event")
        }
    }

    // MARK: single flight

    func testConcurrentFlushRequestsResultInOneActiveFlight() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { request in
            let sequences = request.sampleSequenceNumbers
            return .acknowledged(sequences: sequences, ingested: sequences.count, delay: 0.15)
        }
        record(uploader, count: 200)
        await uploader.waitForPendingWrites()

        await withTaskGroup(of: Void.self) { group in
            for _ in 0..<5 { group.addTask { await uploader.flushPending() } }
        }

        XCTAssertEqual(stub.maxConcurrentRequests, 1, "only one upload may be in flight at a time")
        let sent = sampleUploads.flatMap(\.sampleSequenceNumbers)
        XCTAssertEqual(sent.count, Set(sent).count, "no sequence was sent twice by overlapping flushes")
        XCTAssertEqual(try pendingSequences(store), [])
    }

    // MARK: lifecycle

    func testStopDoesNotLoseTheTail() async throws {
        let (uploader, _) = makeUploader()
        stub.setHandler { _ in .offline }
        record(uploader, count: 10)
        await uploader.waitForPendingWrites()

        // Today stop() only cancels the timer; a single best-effort flush happens by accident
        // (the cancelled sleep is swallowed by `try?`). Offline, that one attempt fails and the
        // tail then exists only in RAM — the loss this test pins down.
        uploader.stop()

        // Restart: a new store + uploader on the same directory still has all 10.
        StubServer.state.reset()
        acknowledgeEverything()
        let (restarted, restartedStore) = makeUploader()
        XCTAssertEqual(try pendingSequences(restartedStore), Array(1...10).map { Optional($0) })
        await restarted.flushPending()
        XCTAssertEqual(sampleUploads.flatMap(\.sampleSequenceNumbers), Array(1...10))
        XCTAssertEqual(try pendingSequences(restartedStore), [])
    }

    func testPendingTelemetryRemainsAvailableAfterSessionCompletion() async throws {
        let (uploader, store) = makeUploader()
        stub.setHandler { _ in .offline }
        record(uploader, count: 3)
        uploader.recordEvent(type: "recording_stopped", details: [:])
        await uploader.waitForPendingWrites()

        uploader.stop()                       // the session ended (the app then calls /complete)
        try store.finishSession(session)

        XCTAssertEqual(makeStore().sessionsWithPendingTelemetry(), [session])
        StubServer.state.reset()
        acknowledgeEverything()
        let (later, laterStore) = makeUploader()
        await later.flushPending()

        XCTAssertEqual(sampleUploads.flatMap(\.sampleSequenceNumbers), [1, 2, 3])
        XCTAssertEqual(eventUploads.count, 1)
        XCTAssertEqual(laterStore.sessionsWithPendingTelemetry(), [], "cleaned up only after everything was acknowledged")
    }
}
