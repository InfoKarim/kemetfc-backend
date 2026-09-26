//
//  TelemetryQueueStoreTests.swift
//  KemetFCTrackerTests
//
//  STATUS: written BEFORE the implementation (tests-first). These tests do
//  not compile until the interface below exists — that is expected and is
//  the first "failure" this step reports.
//
//  TARGET INTERFACE (new file Sources/Networking/TelemetryQueueStore.swift)
//  ------------------------------------------------------------------------
//  public final class TelemetryQueueStore: @unchecked Sendable {
//      init(rootDirectory: URL, warningThresholdBytes: Int64 = 150 * 1024 * 1024)
//      // Persists, then returns. The returned sample carries the sequence
//      // number that was assigned ONCE and written to disk with it.
//      func appendSample(_ sample: TrackingSample, sessionId: String) throws -> TrackingSample
//      func appendEvent(type: String, details: [String: TelemetryEventValue],
//                       occurredAt: Date, sessionId: String) throws -> QueuedTelemetryEvent
//      // Unacknowledged samples in ascending sequence order.
//      func pendingSamples(sessionId: String, limit: Int) throws -> [TrackingSample]
//      func pendingEvents(sessionId: String) throws -> [QueuedTelemetryEvent]
//      // Deletes ONLY what the backend positively proved durable: the cursor advances
//      // over the contiguous run of acknowledged sequences starting at cursor+1;
//      // an acknowledged sequence beyond a gap stays pending. Never moves backwards.
//      func acknowledgeSamples(sessionId: String, sequences: [Int]) throws
//      func acknowledgeEvent(sessionId: String, clientEventId: String) throws
//      // The session ended: its directory is removed once EVERYTHING is
//      // acknowledged (never before, never by age or size).
//      func finishSession(_ sessionId: String) throws
//      func sessionsWithPendingTelemetry() -> [String]
//      func sessionDirectory(for sessionId: String) -> URL
//      func sampleSegmentURLs(sessionId: String) -> [URL]
//      // Observability only: crossing the threshold NEVER drops data.
//      var onStorageWarning: ((_ sessionId: String, _ bytes: Int64) -> Void)? { get set }
//      func isOverWarningThreshold(sessionId: String) -> Bool
//  }
//  public struct QueuedTelemetryEvent: Codable, Equatable, Sendable {
//      var clientEventId: String        // UUID string generated ONCE at enqueue
//      var eventType: String
//      var details: [String: TelemetryEventValue]
//      var clientOccurredAt: Date       // device time; server receipt time is separate
//  }
//  TrackingSample gains `var sequenceNumber: Int?` (wire key "sequence_number").
//  TelemetryEventValue gains Decodable + Equatable (it is Encodable only today).
//
//  NOT TESTABLE ON macOS: iOS file-protection class (NSFileProtection*) —
//  needs a device; see the report.
//

import XCTest
@testable import KemetFCTracker

final class TelemetryQueueStoreTests: TelemetryTestCase {
    private let session = "TRK_0001"

    private func makeStore(threshold: Int64 = 150 * 1024 * 1024) -> TelemetryQueueStore {
        TelemetryQueueStore(rootDirectory: root, warningThresholdBytes: threshold)
    }

    private func sequences(_ store: TelemetryQueueStore, session: String? = nil) throws -> [Int?] {
        try store.pendingSamples(sessionId: session ?? self.session, limit: 100_000).map(\.sequenceNumber)
    }

    // MARK: durability

    func testEnqueueWritesDurablyBeforeReturning() throws {
        let store = makeStore()

        let stored = try store.appendSample(makeSample(t: 1.5), sessionId: session)

        XCTAssertEqual(stored.sequenceNumber, 1)
        // A brand-new instance shares no memory with `store`: whatever it can
        // read was on disk when appendSample returned.
        let pending = try makeStore().pendingSamples(sessionId: session, limit: 10)
        XCTAssertEqual(pending.map(\.sequenceNumber), [1])
        XCTAssertEqual(pending.first?.tSeconds, 1.5)
        XCTAssertEqual(pending.first?.playerBbox, [0.4, 0.3, 0.15, 0.4])
    }

    func testSequenceNumbersAreAssignedOnceAndAreContiguous() throws {
        let store = makeStore()
        let assigned = try (1...5).map { try store.appendSample(makeSample(t: Double($0)), sessionId: session).sequenceNumber }
        XCTAssertEqual(assigned, [1, 2, 3, 4, 5])
        XCTAssertEqual(try sequences(store), [1, 2, 3, 4, 5])
    }

    func testSequenceNumbersSurviveStoreRecreation() throws {
        let first = makeStore()
        for t in 1...3 { _ = try first.appendSample(makeSample(t: Double(t)), sessionId: session) }

        let restarted = makeStore()
        XCTAssertEqual(try sequences(restarted), [1, 2, 3])
        XCTAssertEqual(try restarted.appendSample(makeSample(t: 4), sessionId: session).sequenceNumber, 4)
    }

    func testSequenceNumbersStayMonotonicAfterEverythingWasAcknowledged() throws {
        // If the cursor were lost together with the acknowledged data, the next
        // sample would reuse sequence 1 and the backend would call it a duplicate.
        let first = makeStore()
        for t in 1...3 { _ = try first.appendSample(makeSample(t: Double(t)), sessionId: session) }
        try first.acknowledgeSamples(sessionId: session, sequences: Array(1...3))

        let restarted = makeStore()

        XCTAssertEqual(try restarted.appendSample(makeSample(t: 4), sessionId: session).sequenceNumber, 4)
        XCTAssertEqual(try sequences(restarted), [4])
    }

    func testSequenceNumbersAreScopedPerSession() throws {
        let store = makeStore()
        _ = try store.appendSample(makeSample(t: 1), sessionId: "TRK_A")
        _ = try store.appendSample(makeSample(t: 2), sessionId: "TRK_A")

        XCTAssertEqual(try store.appendSample(makeSample(t: 1), sessionId: "TRK_B").sequenceNumber, 1)
        XCTAssertEqual(try sequences(store, session: "TRK_A"), [1, 2])
    }

    // MARK: acknowledgment cursor

    func testAcknowledgmentRemovesOnlyAcknowledgedSamplesAndPersists() throws {
        let store = makeStore()
        for t in 1...5 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        try store.acknowledgeSamples(sessionId: session, sequences: Array(1...3))

        XCTAssertEqual(try sequences(store), [4, 5])
        XCTAssertEqual(try sequences(makeStore()), [4, 5], "the cursor must be on disk, not only in memory")
    }

    func testAcknowledgmentIsIdempotentAndNeverMovesBackwards() throws {
        let store = makeStore()
        for t in 1...5 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        try store.acknowledgeSamples(sessionId: session, sequences: Array(1...3))
        try store.acknowledgeSamples(sessionId: session, sequences: Array(1...3))
        try store.acknowledgeSamples(sessionId: session, sequences: Array(1...1))

        XCTAssertEqual(try sequences(store), [4, 5])
    }

    func testAcknowledgingBeyondAGapDoesNotDeleteTheUnprovenSamples() throws {
        let store = makeStore()
        for t in 1...6 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        try store.acknowledgeSamples(sessionId: session, sequences: [2, 3])     // 1 is NOT proven durable

        XCTAssertEqual(try sequences(store), [1, 2, 3, 4, 5, 6], "nothing may be deleted while sequence 1 is unproven")

        try store.acknowledgeSamples(sessionId: session, sequences: [1])
        XCTAssertEqual(try sequences(store), [2, 3, 4, 5, 6], "1 is proven, but 2 and 3 were only acknowledged in an earlier response")
        try store.acknowledgeSamples(sessionId: session, sequences: [2, 3])
        XCTAssertEqual(try sequences(store), [4, 5, 6])
    }

    func testAcknowledgedSequenceAfterAGapStaysPendingUntilTheGapIsProven() throws {
        let store = makeStore()
        for t in 1...5 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        try store.acknowledgeSamples(sessionId: session, sequences: [1, 2, 3, 5])   // 4 unproven

        XCTAssertEqual(try sequences(makeStore()), [4, 5], "the cursor stops at 3; 5 is resent later (a harmless duplicate)")
    }

    func testAcknowledgingUnknownSequencesChangesNothing() throws {
        let store = makeStore()
        for t in 1...3 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        try store.acknowledgeSamples(sessionId: session, sequences: [99, 100])
        try store.acknowledgeSamples(sessionId: session, sequences: [])

        XCTAssertEqual(try sequences(store), [1, 2, 3])
    }

    func testPendingSamplesHonorTheLimitInSequenceOrder() throws {
        let store = makeStore()
        for t in 1...10 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        XCTAssertEqual(try store.pendingSamples(sessionId: session, limit: 4).map(\.sequenceNumber), [1, 2, 3, 4])
    }

    // MARK: crash safety

    func testTornFinalLineDoesNotDestroyEarlierRecords() throws {
        let store = makeStore()
        for t in 1...3 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }
        try appendRawBytes("{\"t_seconds\":4.0,\"player_bb", toLastSegmentOf: store)   // process died mid-write

        let restarted = makeStore()

        XCTAssertEqual(try sequences(restarted), [1, 2, 3])
    }

    func testAppendAfterATornLineDoesNotMergeWithTheGarbage() throws {
        let store = makeStore()
        for t in 1...3 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }
        try appendRawBytes("{\"t_seconds\":4.0,\"player_bb", toLastSegmentOf: store)

        let restarted = makeStore()
        let next = try restarted.appendSample(makeSample(t: 4.5), sessionId: session)

        XCTAssertEqual(next.sequenceNumber, 4)
        let readBack = try makeStore().pendingSamples(sessionId: session, limit: 100)
        XCTAssertEqual(readBack.map(\.sequenceNumber), [1, 2, 3, 4])
        XCTAssertEqual(readBack.last?.tSeconds, 4.5)
    }

    private func appendRawBytes(_ text: String, toLastSegmentOf store: TelemetryQueueStore) throws {
        let segment = try XCTUnwrap(store.sampleSegmentURLs(sessionId: session).last)
        let handle = try FileHandle(forWritingTo: segment)
        try handle.seekToEnd()
        try handle.write(contentsOf: Data(text.utf8))
        try handle.close()
    }

    // MARK: concurrency + performance

    func testConcurrentAppendsGiveUniqueContiguousSequencesAndNoTornLines() throws {
        let store = makeStore()
        let assignedBox = SequenceBox()
        let sessionId = session
        let sample = makeSample(t: 0)

        DispatchQueue.concurrentPerform(iterations: 8) { _ in
            for _ in 0..<50 {
                let stored = try? store.appendSample(sample, sessionId: sessionId)
                assignedBox.add(stored?.sequenceNumber ?? -1)
            }
        }
        let assigned = assignedBox.values

        XCTAssertEqual(assigned.sorted(), Array(1...400), "every append gets a unique sequence, none is lost")
        XCTAssertEqual(try sequences(makeStore()), (1...400).map { Optional($0) }, "no interleaved or torn lines on disk")
    }

    func testAppendLatencyStaysFarBelowTheFrameBudget() throws {
        // Guards against a pathological design (e.g. rewriting the whole file
        // per sample). At ~15 Hz the whole camera-path budget is ~66 ms; the
        // durable append must be a small fraction of that. Generous bound:
        // real device numbers come from the device check, not this test.
        let store = makeStore()
        let count = 2_000
        let start = CFAbsoluteTimeGetCurrent()
        for t in 0..<count { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }
        let averageMilliseconds = (CFAbsoluteTimeGetCurrent() - start) * 1000 / Double(count)

        XCTAssertLessThan(averageMilliseconds, 5.0, "average durable append took \(averageMilliseconds) ms")
    }

    // MARK: disk policy

    func testWarningThresholdWarnsButNeverDropsUnacknowledgedTelemetry() throws {
        let store = makeStore(threshold: 4_000)
        let warnings = WarningBox()
        store.onStorageWarning = { sessionId, bytes in warnings.add(sessionId, bytes) }

        for t in 1...60 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        XCTAssertFalse(warnings.entries.isEmpty, "crossing the threshold must be reported")
        XCTAssertEqual(warnings.entries.first?.0, session)
        XCTAssertTrue(store.isOverWarningThreshold(sessionId: session))
        XCTAssertEqual(try sequences(store), (1...60).map { Optional($0) }, "nothing may be dropped")
        XCTAssertEqual(
            try store.appendSample(makeSample(t: 61), sessionId: session).sequenceNumber, 61,
            "the queue keeps accepting data past the warning threshold"
        )
    }

    func testUnacknowledgedTelemetryIsNeverDeletedByAge() throws {
        let store = makeStore()
        for t in 1...3 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }
        let directory = store.sessionDirectory(for: session)
        let longAgo = Date(timeIntervalSinceNow: -60 * 60 * 24 * 90)   // 90 days
        for url in try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) {
            try FileManager.default.setAttributes([.modificationDate: longAgo, .creationDate: longAgo], ofItemAtPath: url.path)
        }

        let restarted = makeStore()

        XCTAssertEqual(try sequences(restarted), [1, 2, 3])
        XCTAssertEqual(restarted.sessionsWithPendingTelemetry(), [session])
    }

    func testFinishedSessionIsRemovedOnlyAfterFullAcknowledgment() throws {
        let store = makeStore()
        for t in 1...3 { _ = try store.appendSample(makeSample(t: Double(t)), sessionId: session) }

        try store.finishSession(session)
        XCTAssertEqual(store.sessionsWithPendingTelemetry(), [session], "a finished session keeps its unacknowledged data")
        XCTAssertEqual(try sequences(makeStore()), [1, 2, 3])

        try store.acknowledgeSamples(sessionId: session, sequences: Array(1...2))
        XCTAssertEqual(store.sessionsWithPendingTelemetry(), [session])

        try store.acknowledgeSamples(sessionId: session, sequences: Array(1...3))
        XCTAssertEqual(store.sessionsWithPendingTelemetry(), [])
        XCTAssertFalse(FileManager.default.fileExists(atPath: store.sessionDirectory(for: session).path))
    }

    func testSessionsWithPendingTelemetryListsOnlySessionsWithUnacknowledgedData() throws {
        let store = makeStore()
        _ = try store.appendSample(makeSample(t: 1), sessionId: "TRK_A")
        _ = try store.appendSample(makeSample(t: 1), sessionId: "TRK_B")
        try store.acknowledgeSamples(sessionId: "TRK_A", sequences: Array(1...1))

        XCTAssertEqual(Set(makeStore().sessionsWithPendingTelemetry()), ["TRK_B"])
    }

    // MARK: privacy

    func testQueueIsExcludedFromBackupAndNamedBySessionIdOnly() throws {
        let store = makeStore()
        _ = try store.appendSample(makeSample(t: 1), sessionId: session)

        let directory = store.sessionDirectory(for: session)
        XCTAssertEqual(directory.lastPathComponent, session, "no player name or other PII in the path")
        let values = try directory.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(values.isExcludedFromBackup, true, "child tracking data must not go to iCloud backup")
    }

    // MARK: events

    func testEventEnqueuePersistsAStableClientEventId() throws {
        let store = makeStore()
        let happenedAt = Date(timeIntervalSince1970: 1_800_000_000)

        let event = try store.appendEvent(
            type: "player_locked", details: ["target_track_id": .int(1), "mode": .string("player_lock")],
            occurredAt: happenedAt, sessionId: session
        )

        XCTAssertNotNil(UUID(uuidString: event.clientEventId), "client_event_id is a UUID")
        let persisted = try makeStore().pendingEvents(sessionId: session)
        XCTAssertEqual(persisted.map(\.clientEventId), [event.clientEventId])
        XCTAssertEqual(persisted.first?.eventType, "player_locked")
        XCTAssertEqual(persisted.first?.details["target_track_id"], .int(1))
        XCTAssertEqual(persisted.first?.details["mode"], .string("player_lock"))
        XCTAssertEqual(persisted.first!.clientOccurredAt.timeIntervalSince(happenedAt), 0, accuracy: 1.0)
    }

    func testEventIdentityIsUnchangedAcrossStoreRecreation() throws {
        let first = makeStore()
        let event = try first.appendEvent(type: "mode_switch", details: [:], occurredAt: Date(), sessionId: session)

        let secondRead = try makeStore().pendingEvents(sessionId: session)
        let thirdRead = try makeStore().pendingEvents(sessionId: session)

        XCTAssertEqual(secondRead.map(\.clientEventId), [event.clientEventId])
        XCTAssertEqual(thirdRead.map(\.clientEventId), [event.clientEventId], "re-reading never mints a new id")
    }

    func testTwoEventsGetDistinctIdsAndAcknowledgingOneKeepsTheOther() throws {
        let store = makeStore()
        let first = try store.appendEvent(type: "a", details: [:], occurredAt: Date(), sessionId: session)
        let second = try store.appendEvent(type: "b", details: [:], occurredAt: Date(), sessionId: session)
        XCTAssertNotEqual(first.clientEventId, second.clientEventId)

        try store.acknowledgeEvent(sessionId: session, clientEventId: first.clientEventId)

        XCTAssertEqual(try makeStore().pendingEvents(sessionId: session).map(\.clientEventId), [second.clientEventId])
    }

    func testEventsAloneKeepASessionPending() throws {
        let store = makeStore()
        let event = try store.appendEvent(type: "a", details: [:], occurredAt: Date(), sessionId: session)
        try store.finishSession(session)
        XCTAssertEqual(store.sessionsWithPendingTelemetry(), [session])

        try store.acknowledgeEvent(sessionId: session, clientEventId: event.clientEventId)

        XCTAssertEqual(store.sessionsWithPendingTelemetry(), [])
    }
}

private final class WarningBox: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [(String, Int64)] = []
    func add(_ sessionId: String, _ bytes: Int64) { lock.withLock { storage.append((sessionId, bytes)) } }
    var entries: [(String, Int64)] { lock.withLock { storage } }
}

private final class SequenceBox: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [Int] = []
    func add(_ value: Int) { lock.withLock { storage.append(value) } }
    var values: [Int] { lock.withLock { storage } }
}
