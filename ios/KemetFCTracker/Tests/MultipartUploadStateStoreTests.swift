//
//  MultipartUploadStateStoreTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 10's persistence/state items (2026-10-01, Phase 2).
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class MultipartUploadStateStoreTests: XCTestCase {
    private var root: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        root = FileManager.default.temporaryDirectory.appendingPathComponent("kemet-mp-state-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
        try super.tearDownWithError()
    }

    private func sampleState(recordingId: String = "REC-1", totalParts: Int = 3) -> MultipartUploadState {
        MultipartUploadState(
            recordingId: recordingId, videoId: "VID-\(recordingId)", recordId: "RECID-\(recordingId)",
            sessionId: "SESSION-1", multipartUploadId: "upload-1", objectKey: "\(recordingId).mov",
            partSizeBytes: 32 * 1024 * 1024, totalParts: totalParts
        )
    }

    // MARK: State persistence / reload

    func testStateSurvivesStoreReload() {
        let store = MultipartUploadStateStore(rootDirectory: root)
        store.upsert(sampleState())

        let reloaded = MultipartUploadStateStore(rootDirectory: root)
        XCTAssertEqual(reloaded.state(for: "REC-1")?.multipartUploadId, "upload-1")
    }

    func testCompletedPartPersistsImmediately() {
        let store = MultipartUploadStateStore(rootDirectory: root)
        store.upsert(sampleState())
        store.recordCompletedPart(recordingId: "REC-1", partNumber: 1, etag: "etag-1")

        let reloaded = MultipartUploadStateStore(rootDirectory: root)
        XCTAssertEqual(reloaded.state(for: "REC-1")?.completedParts[1], "etag-1")
    }

    func testAttemptsPerPartTracked() {
        let store = MultipartUploadStateStore(rootDirectory: root)
        store.upsert(sampleState())
        store.recordAttempt(recordingId: "REC-1", partNumber: 2)
        store.recordAttempt(recordingId: "REC-1", partNumber: 2)
        XCTAssertEqual(store.state(for: "REC-1")?.attemptsPerPart[2], 2)
    }

    func testRemoveStateClearsItPermanently() {
        let store = MultipartUploadStateStore(rootDirectory: root)
        store.upsert(sampleState())
        store.removeState(for: "REC-1")
        XCTAssertNil(store.state(for: "REC-1"))

        let reloaded = MultipartUploadStateStore(rootDirectory: root)
        XCTAssertNil(reloaded.state(for: "REC-1"))
    }

    func testMultipleRecordingsTrackedIndependently() {
        let store = MultipartUploadStateStore(rootDirectory: root)
        store.upsert(sampleState(recordingId: "REC-A"))
        store.upsert(sampleState(recordingId: "REC-B"))
        store.recordCompletedPart(recordingId: "REC-A", partNumber: 1, etag: "a1")
        XCTAssertNil(store.state(for: "REC-B")?.completedParts[1])
    }

    // MARK: MultipartUploadState derived properties

    func testIsCompleteRequiresEveryPart() {
        var state = sampleState(totalParts: 3)
        state.completedParts = [1: "a", 2: "b"]
        XCTAssertFalse(state.isComplete)
        state.completedParts[3] = "c"
        XCTAssertTrue(state.isComplete)
    }

    func testFirstIncompletePartSkipsCompletedOnes() {
        var state = sampleState(totalParts: 5)
        state.completedParts = [1: "a", 2: "b"]
        XCTAssertEqual(state.firstIncompletePart, 3)
    }

    func testIncompletePartNumbersExcludesCompleted() {
        var state = sampleState(totalParts: 4)
        state.completedParts = [2: "b"]
        XCTAssertEqual(state.incompletePartNumbers, [1, 3, 4])
    }

    func testCorruptIndexRecoversFromBackup() {
        let store = MultipartUploadStateStore(rootDirectory: root)
        store.upsert(sampleState())
        store.upsert(sampleState(recordingId: "REC-2")) // creates a backup of the first write

        // Corrupt the primary index file directly.
        try! "not valid json".write(to: root.appendingPathComponent("multipart_uploads.json"), atomically: true, encoding: .utf8)

        let recovered = MultipartUploadStateStore(rootDirectory: root)
        XCTAssertNotNil(recovered.state(for: "REC-1"), "must recover from the backup rather than losing all state")
    }
}
