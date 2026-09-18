//
//  PendingAssessmentStoreTests.swift
//  KemetFCTrackerTests
//
//  Every test here uses its OWN unique temporary directory, injected via
//  PendingAssessmentStore(rootDirectory:) — never the real app Documents
//  directory. This replaces an earlier version of this file that used
//  the production Documents path (PendingAssessmentStore.documentsDirectory,
//  now `defaultDocumentsDirectory`, which this file never references):
//  a physical-device test run pulled the REAL app's Documents container
//  afterward and found XCTest fixtures (playerId "TEST_PLAYER") mixed
//  directly into the production pending_assessments.json. Each test
//  method gets a fresh UUID-named directory under NSTemporaryDirectory(),
//  guaranteeing tests running in parallel (Xcode's default for test
//  targets) can never share, race on, or pollute each other's — or the
//  real app's — storage.
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class PendingAssessmentStoreTests: XCTestCase {
    private var testRootDirectory: URL!

    override func setUp() {
        super.setUp()
        testRootDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("KemetFCTrackerTests-\(UUID().uuidString)")
    }

    override func tearDown() {
        if let testRootDirectory {
            try? FileManager.default.removeItem(at: testRootDirectory)
        }
        testRootDirectory = nil
        super.tearDown()
    }

    private func makeStore() -> PendingAssessmentStore {
        PendingAssessmentStore(rootDirectory: testRootDirectory)
    }

    private func makeRecord(id: String = UUID().uuidString, playerId: String = "TEST_PLAYER") -> PendingAssessmentRecord {
        PendingAssessmentRecord(
            id: id,
            playerId: playerId,
            playerName: "Test Player",
            localVideoRelativePath: "Recordings/\(id).mov",
            startTime: Date(),
            trackingMode: "smart_soccer"
        )
    }

    /// Writes real, valid MP4/MOV-signature bytes at the record's
    /// expected path — several tests need a file that genuinely passes
    /// existence/size/readability checks, not just a JSON entry claiming
    /// one exists.
    private func writeMinimalValidVideoFile(for record: PendingAssessmentRecord, in store: PendingAssessmentStore) {
        let url = store.localVideoURL(for: record)
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        // A real, tiny, valid ISO-BMFF/QuickTime file signature — enough
        // for FileManager existence/size checks; full AVAsset readability
        // needs a genuine encoded track, which these store-level tests
        // don't need (that's covered by the physical-device recording
        // pipeline itself, not this pure-persistence layer).
        let minimalMOVBytes = Data([0x00, 0x00, 0x00, 0x14, 0x66, 0x74, 0x79, 0x70, 0x71, 0x74, 0x20, 0x20])
        try? minimalMOVBytes.write(to: url)
    }

    // MARK: - Test isolation itself

    func testTestStoreIsIsolatedFromProductionDocuments() {
        let store = makeStore()
        XCTAssertNotEqual(store.rootDirectory, PendingAssessmentStore.defaultDocumentsDirectory)
        XCTAssertTrue(store.rootDirectory.path.hasPrefix(FileManager.default.temporaryDirectory.path))
    }

    func testTwoStoresWithDifferentRootsDoNotShareRecords() {
        let storeA = PendingAssessmentStore(rootDirectory: testRootDirectory.appendingPathComponent("A"))
        let storeB = PendingAssessmentStore(rootDirectory: testRootDirectory.appendingPathComponent("B"))

        storeA.upsert(makeRecord(id: "only-in-A"))

        XCTAssertNotNil(storeA.record(id: "only-in-A"))
        XCTAssertNil(storeB.record(id: "only-in-A"), "a record written to store A's root must not appear in store B's root")
    }

    /// Directly exercises what running the whole test suite in parallel
    /// (Xcode's default) does: many PendingAssessmentStore instances,
    /// each with its OWN root, writing concurrently. Before the
    /// isolation fix, every instance shared one hardcoded path and could
    /// race/overwrite each other's records — this proves that can't
    /// happen anymore, without relying on Xcode's actual scheduler.
    func testParallelWritesToDifferentStoresNeverLoseRecords() async {
        await withTaskGroup(of: Void.self) { group in
            for index in 0..<10 {
                group.addTask { @MainActor in
                    let store = PendingAssessmentStore(rootDirectory: self.testRootDirectory.appendingPathComponent("parallel-\(index)"))
                    store.upsert(self.makeRecord(id: "record-\(index)"))
                }
            }
        }
        for index in 0..<10 {
            let store = PendingAssessmentStore(rootDirectory: testRootDirectory.appendingPathComponent("parallel-\(index)"))
            XCTAssertNotNil(store.record(id: "record-\(index)"), "record \(index) should have survived in its own isolated store")
        }
    }

    // MARK: - Persistence / restart recovery

    func testUpsertPersistsAcrossFreshStoreInstance() {
        let recordingId = UUID().uuidString
        let firstStore = makeStore()
        firstStore.upsert(makeRecord(id: recordingId))

        // A brand-new instance reading the SAME root — this is exactly
        // what happens on a real app relaunch.
        let secondStore = makeStore()
        let recovered = secondStore.record(id: recordingId)
        XCTAssertNotNil(recovered)
        XCTAssertEqual(recovered?.playerId, "TEST_PLAYER")
        XCTAssertEqual(recovered?.uploadState, .recording)
    }

    func testUpsertUpdatesExistingRecordRatherThanDuplicating() {
        let recordingId = UUID().uuidString
        let store = makeStore()
        store.upsert(makeRecord(id: recordingId))

        var updated = store.record(id: recordingId)!
        updated.uploadState = .localSaved
        updated.durationSeconds = 20.0
        store.upsert(updated)

        XCTAssertEqual(store.records.filter { $0.id == recordingId }.count, 1)
        XCTAssertEqual(store.record(id: recordingId)?.uploadState, .localSaved)
        XCTAssertEqual(store.record(id: recordingId)?.durationSeconds, 20.0)
    }

    func testLegacyNumericDateDecodes() throws {
        // The exact format the pre-fix JSONEncoder wrote (Foundation's
        // default .deferredToDate: seconds since the 2001 reference
        // date) — a real device could still have a queue file in this
        // format from before JSONEncoder.kemet.dateEncodingStrategy was
        // fixed to .iso8601.
        let legacyJSON = """
        [{
            "id": "legacy-numeric-date",
            "playerId": "TEST_PLAYER",
            "playerName": "Test Player",
            "localVideoRelativePath": "Recordings/legacy-numeric-date.mov",
            "startTime": 750000000.0,
            "trackingMode": "smart_soccer",
            "uploadState": "LOCAL_SAVED",
            "uploadAttempts": 0
        }]
        """
        // Precreate the index file BEFORE constructing the store — the
        // store only ever reads from disk once, at init (load()), not
        // reactively afterward.
        try FileManager.default.createDirectory(at: testRootDirectory, withIntermediateDirectories: true)
        try legacyJSON.data(using: .utf8)!.write(to: testRootDirectory.appendingPathComponent("pending_assessments.json"))

        let store = makeStore()
        let recovered = store.record(id: "legacy-numeric-date")
        XCTAssertNotNil(recovered, "a legacy numeric-date record should still decode, not be silently dropped")
    }

    func testISO8601DateDecodes() throws {
        try FileManager.default.createDirectory(at: testRootDirectory, withIntermediateDirectories: true)
        let iso8601JSON = """
        [{
            "id": "iso-date",
            "playerId": "TEST_PLAYER",
            "playerName": "Test Player",
            "localVideoRelativePath": "Recordings/iso-date.mov",
            "startTime": "2026-09-18T08:12:52Z",
            "trackingMode": "smart_soccer",
            "uploadState": "LOCAL_SAVED",
            "uploadAttempts": 0
        }]
        """
        try iso8601JSON.data(using: .utf8)!.write(to: testRootDirectory.appendingPathComponent("pending_assessments.json"))

        let store = makeStore()
        XCTAssertNotNil(store.record(id: "iso-date"))
    }

    func testMalformedRecordDoesNotEraseValidEntries() throws {
        try FileManager.default.createDirectory(at: testRootDirectory, withIntermediateDirectories: true)
        let mixedJSON = """
        [
            {
                "id": "valid-record",
                "playerId": "TEST_PLAYER",
                "playerName": "Test Player",
                "localVideoRelativePath": "Recordings/valid-record.mov",
                "startTime": "2026-09-18T08:12:52Z",
                "trackingMode": "smart_soccer",
                "uploadState": "LOCAL_SAVED",
                "uploadAttempts": 0
            },
            {
                "id": "malformed-record",
                "playerId": "TEST_PLAYER"
            }
        ]
        """
        try mixedJSON.data(using: .utf8)!.write(to: testRootDirectory.appendingPathComponent("pending_assessments.json"))

        let store = makeStore()
        XCTAssertNotNil(store.record(id: "valid-record"), "a malformed sibling record must not take a valid record down with it")
        XCTAssertNil(store.record(id: "malformed-record"))
        XCTAssertEqual(store.quarantinedRecordCount, 1)
        XCTAssertNotNil(store.lastPersistenceError, "a quarantine must be surfaced, never silent")
    }

    func testCompletelyUnparseableIndexFileIsQuarantinedNotSilentlyDropped() throws {
        try FileManager.default.createDirectory(at: testRootDirectory, withIntermediateDirectories: true)
        try "not valid json at all {{{".data(using: .utf8)!.write(to: testRootDirectory.appendingPathComponent("pending_assessments.json"))

        let store = makeStore()
        XCTAssertEqual(store.records.count, 0)
        XCTAssertNotNil(store.lastPersistenceError, "an unreadable index must surface an error, never silently report success")
        // The raw unreadable file must be preserved somewhere, not deleted.
        XCTAssertTrue(FileManager.default.fileExists(atPath: testRootDirectory.appendingPathComponent("pending_assessments.quarantine.json").path))
    }

    func testBackupRecoversFromACorruptMainIndex() throws {
        let store = makeStore()
        store.upsert(makeRecord(id: "good-record"))
        // persist() copies the previous valid file to a backup path
        // before writing a new one — corrupt the MAIN file only, leaving
        // the backup (from the first upsert onward) intact.
        var second = store.record(id: "good-record")!
        second.durationSeconds = 5
        store.upsert(second) // now a backup of the FIRST write exists

        try "corrupted-not-json".data(using: .utf8)!.write(to: testRootDirectory.appendingPathComponent("pending_assessments.json"))

        let recoveredStore = makeStore()
        XCTAssertNotNil(recoveredStore.record(id: "good-record"), "a corrupt main index should recover from the backup rather than losing everything")
    }

    // MARK: - State classification (isRetryable / cleanup eligibility)

    func testIsRetryableIncludesLocalSavedPendingUploadAndFailedRetryable() {
        var record = makeRecord()
        record.stopTime = Date()

        record.uploadState = .localSaved
        XCTAssertTrue(record.isRetryable, "LOCAL_SAVED must be retryable — a live physical-device example (B1C50F7F...) was found permanently stranded before this fix")

        record.uploadState = .pendingUpload
        XCTAssertTrue(record.isRetryable)

        record.uploadState = .failedRetryable
        XCTAssertTrue(record.isRetryable)

        record.uploadState = .recording
        XCTAssertFalse(record.isRetryable)

        record.uploadState = .uploading
        XCTAssertFalse(record.isRetryable, "still handled separately — normalized to PENDING_UPLOAD on launch, not treated as directly retryable in place")

        record.uploadState = .uploaded
        XCTAssertFalse(record.isRetryable)

        record.uploadState = .failedPermanent
        XCTAssertFalse(record.isRetryable)
    }

    func testCleanupEligibilityRequiresUploadedAndServerVideoId() {
        var record = makeRecord()
        record.stopTime = Date().addingTimeInterval(-1000)
        record.uploadState = .uploaded
        XCTAssertFalse(record.isEligibleForCleanup, "UPLOADED alone isn't enough without a confirmed serverVideoId")

        record.serverVideoId = "VID-abc"
        XCTAssertTrue(record.isEligibleForCleanup)

        record.uploadState = .failedRetryable
        XCTAssertFalse(record.isEligibleForCleanup, "a failed recording is never eligible for cleanup")
    }

    func testPendingAndFailedFilesAreNeverDeletedByCleanup() {
        let store = makeStore()
        var pending = makeRecord()
        pending.stopTime = Date()
        pending.uploadState = .pendingUpload
        writeMinimalValidVideoFile(for: pending, in: store)
        store.upsert(pending)

        var failed = makeRecord()
        failed.stopTime = Date()
        failed.uploadState = .failedRetryable
        writeMinimalValidVideoFile(for: failed, in: store)
        store.upsert(failed)

        store.deleteLocalFile(recordId: pending.id)
        store.deleteLocalFile(recordId: failed.id)

        XCTAssertTrue(store.fileExistsSync(for: pending), "deleteLocalFile must refuse to delete a PENDING_UPLOAD recording's file")
        XCTAssertTrue(store.fileExistsSync(for: failed), "deleteLocalFile must refuse to delete a FAILED_RETRYABLE recording's file")
    }

    func testCleanupEligibleRecordIdsRespectsSafetyWindow() {
        let store = makeStore()
        var recent = makeRecord()
        recent.stopTime = Date()
        recent.uploadState = .uploaded
        recent.serverVideoId = "VID-recent"

        var old = makeRecord()
        old.stopTime = Date().addingTimeInterval(-1_000_000)
        old.uploadState = .uploaded
        old.serverVideoId = "VID-old"

        store.upsert(recent)
        store.upsert(old)

        let eligible = store.cleanupEligibleRecordIds(safetyWindow: 86_400) // 1 day
        XCTAssertTrue(eligible.contains(old.id))
        XCTAssertFalse(eligible.contains(recent.id), "recently-uploaded recordings stay within the safety window")
    }

    func testActionablePendingCountExcludesFailedPermanentAndUploaded() {
        let store = makeStore()
        var uploaded = makeRecord(); uploaded.uploadState = .uploaded
        var failedPermanent = makeRecord(); failedPermanent.uploadState = .failedPermanent
        var pending = makeRecord(); pending.stopTime = Date(); pending.uploadState = .pendingUpload

        store.upsert(uploaded)
        store.upsert(failedPermanent)
        store.upsert(pending)

        XCTAssertEqual(store.actionablePendingCount, 1, "only the genuinely actionable PENDING_UPLOAD record should count")
    }

    // MARK: - File verification (never claim saved without real checks)

    func testVerifyFileHealthReportsMissingWhenNoFileExists() async {
        let store = makeStore()
        let record = makeRecord()
        store.upsert(record)

        let health = await store.verifyFileHealth(for: record)
        guard case .missing = health else {
            return XCTFail("expected .missing, got \(health)")
        }
    }

    func testVerifyFileHealthReportsEmptyForZeroByteFile() async {
        let store = makeStore()
        let record = makeRecord()
        let url = store.localVideoURL(for: record)
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: url.path, contents: Data())
        store.upsert(record)

        let health = await store.verifyFileHealth(for: record)
        guard case .empty = health else {
            return XCTFail("expected .empty, got \(health)")
        }
    }

    func testVerifyFileHealthReportsUnreadableForGarbageBytes() async {
        let store = makeStore()
        let record = makeRecord()
        let url = store.localVideoURL(for: record)
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? Data([0x01, 0x02, 0x03, 0x04]).write(to: url) // non-empty, but not a real video container
        store.upsert(record)

        let health = await store.verifyFileHealth(for: record)
        guard case .unreadable = health else {
            return XCTFail("expected .unreadable, got \(health)")
        }
    }

    func testNewRecordingFileURLAndRelativePathRoundTrip() {
        let store = makeStore()
        let recordingId = UUID().uuidString
        let fileURL = store.newRecordingFileURL(recordingId: recordingId)

        XCTAssertTrue(fileURL.path.contains("Recordings"))
        XCTAssertTrue(fileURL.path.hasSuffix("\(recordingId).mov"))

        let relative = store.relativePath(for: fileURL)
        XCTAssertFalse(relative.hasPrefix("/"), "must be relative to the store's root, never absolute")

        var record = makeRecord(id: recordingId)
        record.localVideoRelativePath = relative
        XCTAssertEqual(store.localVideoURL(for: record).path, fileURL.path)
    }
}
