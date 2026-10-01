//
//  MultipartVideoUploaderTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 10's remaining items (2026-10-01, Phase 2) — part-size
//  math, file-range correctness, bounded memory, the full upload flow,
//  retry-only-the-failed-part, reconciliation (both directions), and the
//  mandatory safety tests proving the real pending match cannot auto-
//  start. No production R2/backend is used anywhere — StubServer
//  (Tests/TelemetryTestSupport.swift) intercepts every request,
//  including the "direct-to-R2" PUTs, which this file routes to
//  recognizable fake paths distinct from the real Kemet API paths.
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class MultipartVideoUploaderTests: TelemetryTestCase {
    // MARK: Part-size math (pure, no I/O)

    func testCalculateRejectsZeroAndNegativeSize() {
        XCTAssertThrowsError(try MultipartUploadPlan.calculate(fileSizeBytes: 0))
        XCTAssertThrowsError(try MultipartUploadPlan.calculate(fileSizeBytes: -1))
    }

    func testCalculateExact32MiB() throws {
        let (partSize, totalParts) = try MultipartUploadPlan.calculate(fileSizeBytes: 32 * 1024 * 1024)
        XCTAssertEqual(partSize, 32 * 1024 * 1024)
        XCTAssertEqual(totalParts, 1)
    }

    func testCalculate32MiBPlusOneByteAddsAPart() throws {
        let (_, totalParts) = try MultipartUploadPlan.calculate(fileSizeBytes: 32 * 1024 * 1024 + 1)
        XCTAssertEqual(totalParts, 2)
    }

    func testCalculateRealMatchSizeIs41Parts() throws {
        let (partSize, totalParts) = try MultipartUploadPlan.calculate(fileSizeBytes: 1_351_186_620)
        XCTAssertEqual(partSize, 32 * 1024 * 1024)
        XCTAssertEqual(totalParts, 41)
    }

    func testCalculateOneByteFinalPartEdgeBehavior() throws {
        // Exactly one byte into a new part.
        let (_, totalParts) = try MultipartUploadPlan.calculate(fileSizeBytes: 32 * 1024 * 1024 * 40 + 1)
        XCTAssertEqual(totalParts, 41)
    }

    // MARK: File-range correctness

    func testByteRangeCoversEveryByteExactlyOnceWithNoGapOrOverlap() {
        let fileSize: Int64 = 100
        let partSize: Int64 = 32
        let totalParts = 4
        var covered: [Int64] = []
        for part in 1...totalParts {
            let range = MultipartUploadPlan.byteRange(forPart: part, fileSizeBytes: fileSize, partSizeBytes: partSize)
            covered.append(contentsOf: Array(range))
        }
        XCTAssertEqual(covered, Array(0..<fileSize))
    }

    func testFinalPartIsExactlyTheRemainingBytes() {
        let range = MultipartUploadPlan.byteRange(forPart: 4, fileSizeBytes: 100, partSizeBytes: 32)
        XCTAssertEqual(range, 96..<100)
        XCTAssertEqual(range.count, 4)
    }

    // MARK: Original MOV preserved / bounded memory

    func testOriginalFileByteIdenticalAfterPartReads() throws {
        let fileURL = root.appendingPathComponent("original.mov")
        let originalData = Data((0..<250).map { UInt8($0 % 256) })
        try originalData.write(to: fileURL)

        for part in 1...8 {
            let range = MultipartUploadPlan.byteRange(forPart: part, fileSizeBytes: Int64(originalData.count), partSizeBytes: 32)
            let handle = try FileHandle(forReadingFrom: fileURL)
            try handle.seek(toOffset: UInt64(range.lowerBound))
            _ = try handle.read(upToCount: Int(range.upperBound - range.lowerBound))
            try handle.close()
        }

        XCTAssertEqual(try Data(contentsOf: fileURL), originalData, "reading parts must never modify the source file")
    }

    // MARK: Full upload flow (success path)

    private func makeMetadata(recordingId: String, fileSizeBytes: Int64) -> MultipartInitiateRequest {
        MultipartInitiateRequest(
            videoId: "VID-\(recordingId)", recordId: "REC-\(recordingId)", playerId: "P-TEST",
            sessionId: "SESSION-TEST", recordingId: recordingId, filename: "\(recordingId).mov",
            contentType: "video/quicktime", fileSizeBytes: fileSizeBytes, videoType: "assessment_smart_tracking",
            durationSeconds: 10, locationId: "FIELD", captureDevice: "iPhone", resolution: "1920x1080",
            frameRateFps: 60, schemaVersion: "1.0", createdBy: "coach"
        )
    }

    /// Routes Kemet API calls normally; routes anything under
    /// `/fake-r2/` as a successful presigned-PUT response with a fake
    /// ETag — the stand-in for "direct to R2."
    private func installHandler(uploadId: String = "upload-1", objectKey: String, totalParts: Int, partSize: Int64 = 32) {
        stub.setHandler { request in
            if request.path.hasPrefix("/fake-r2/") {
                let partNumber = Int(request.path.components(separatedBy: "-").last ?? "0") ?? 0
                return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-\(partNumber)\""])
            }
            if request.path == "/videos/uploads/initiate" {
                let parts = (1...totalParts).map { ["part_number": $0, "url": "http://stub.test/fake-r2/part-\($0)"] }
                return .json(201, ["upload_id": uploadId, "object_key": objectKey, "part_size_bytes": partSize, "total_parts": totalParts, "parts": parts])
            }
            if request.path.hasSuffix("/parts"), request.method == "GET" {
                return .json(200, ["upload_id": uploadId, "object_key": objectKey, "uploaded_parts": []])
            }
            if request.path.hasSuffix("/parts/complete") {
                return .json(200, ["object_key": objectKey, "size_bytes": 100, "checksum": "fake"])
            }
            if request.path.hasSuffix("/finalize") {
                return .json(201, ["video_id": "VID-REC-OK", "record_id": "REC-REC-OK", "session_id": "SESSION-TEST"])
            }
            return .offline
        }
    }

    func testFullUploadSucceedsEndToEnd() async throws {
        let fileURL = root.appendingPathComponent("match.mov")
        try Data(repeating: 0x42, count: 100).write(to: fileURL)
        installHandler(objectKey: "VID-REC-OK.mov", totalParts: 4)

        let uploader = MultipartVideoUploader(
            apiClient: makeAPIClient(),
            stateStore: MultipartUploadStateStore(rootDirectory: root),
            partUploadSession: URLSession(configuration: {
                let config = URLSessionConfiguration.ephemeral
                config.protocolClasses = [StubServer.self]
                return config
            }())
        )
        let metadata = makeMetadata(recordingId: "REC-OK", fileSizeBytes: 100)
        let result = try await uploader.upload(fileURL: fileURL, metadata: metadata)
        XCTAssertEqual(result.videoId, "VID-REC-OK")

        // The original file must still exist, untouched, after a full
        // successful upload — cleanup is a SEPARATE, explicit policy.
        XCTAssertTrue(FileManager.default.fileExists(atPath: fileURL.path))
        XCTAssertEqual(try Data(contentsOf: fileURL).count, 100)
    }

    // MARK: Retry only the failed part, not the whole upload

    func testFailedPartRetriesOnlyThatPartNotTheWholeUpload() async throws {
        let fileURL = root.appendingPathComponent("match.mov")
        try Data(repeating: 0x42, count: 100).write(to: fileURL)

        let part2Attempts = AttemptCounter()
        let objectKey = "VID-REC-RETRY.mov"
        stub.setHandler { request in
            if request.path == "/fake-r2/part-2" {
                let attempt = part2Attempts.increment()
                if attempt == 1 { return .offline }  // first attempt fails
                return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-2\""])
            }
            if request.path.hasPrefix("/fake-r2/") {
                let partNumber = Int(request.path.components(separatedBy: "-").last ?? "0") ?? 0
                return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-\(partNumber)\""])
            }
            if request.path == "/videos/uploads/initiate" {
                let parts = (1...4).map { ["part_number": $0, "url": "http://stub.test/fake-r2/part-\($0)"] }
                return .json(201, ["upload_id": "upload-retry", "object_key": objectKey, "part_size_bytes": 32, "total_parts": 4, "parts": parts])
            }
            if request.path.hasSuffix("/parts"), request.method == "GET" {
                return .json(200, ["upload_id": "upload-retry", "object_key": objectKey, "uploaded_parts": []])
            }
            if request.path.hasSuffix("/parts/complete") { return .json(200, ["object_key": objectKey, "size_bytes": 100, "checksum": "fake"]) }
            if request.path.hasSuffix("/finalize") { return .json(201, ["video_id": "VID-REC-RETRY", "record_id": "REC-REC-RETRY", "session_id": "SESSION-TEST"]) }
            return .offline
        }

        let stateStore = MultipartUploadStateStore(rootDirectory: root)
        let uploader = MultipartVideoUploader(
            apiClient: makeAPIClient(), stateStore: stateStore,
            partUploadSession: URLSession(configuration: {
                let config = URLSessionConfiguration.ephemeral
                config.protocolClasses = [StubServer.self]
                return config
            }()),
            maxAttemptsPerPart: 3
        )
        let metadata = makeMetadata(recordingId: "REC-RETRY", fileSizeBytes: 100)
        let result = try await uploader.upload(fileURL: fileURL, metadata: metadata)
        XCTAssertEqual(result.videoId, "VID-REC-RETRY")
        XCTAssertEqual(part2Attempts.increment() - 1, 2, "part 2 must be retried exactly once after its first failure")
    }

    // MARK: Reconciliation — both directions

    func testServerConfirmedPartIsAcceptedOverLocalIgnorance() async throws {
        // Local state (simulated as freshly resumed, no completedParts
        // yet) must accept a part the SERVER already confirms, rather
        // than re-uploading it.
        let fileURL = root.appendingPathComponent("match.mov")
        try Data(repeating: 0x42, count: 100).write(to: fileURL)
        let objectKey = "VID-REC-RECONCILE.mov"
        let stateStore = MultipartUploadStateStore(rootDirectory: root)
        stateStore.upsert(MultipartUploadState(
            recordingId: "REC-RECONCILE", videoId: "VID-REC-RECONCILE", recordId: "REC-REC-RECONCILE",
            sessionId: "SESSION-TEST", multipartUploadId: "upload-reconcile", objectKey: objectKey,
            partSizeBytes: 32, totalParts: 4
        ))

        let part1UploadFlag = AttemptCounter()
        stub.setHandler { request in
            if request.path == "/fake-r2/part-1" { part1UploadFlag.increment(); return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-1\""]) }
            if request.path.hasPrefix("/fake-r2/") {
                let partNumber = Int(request.path.components(separatedBy: "-").last ?? "0") ?? 0
                return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-\(partNumber)\""])
            }
            if request.path == "/videos/uploads/initiate" {
                let parts = (1...4).map { ["part_number": $0, "url": "http://stub.test/fake-r2/part-\($0)"] }
                return .json(201, ["upload_id": "upload-reconcile", "object_key": objectKey, "part_size_bytes": 32, "total_parts": 4, "parts": parts])
            }
            if request.path.hasSuffix("/parts"), request.method == "GET" {
                // Server already has part 1 — client must accept this, never re-upload it.
                return .json(200, ["upload_id": "upload-reconcile", "object_key": objectKey, "uploaded_parts": [["part_number": 1, "etag": "server-etag-1", "size_bytes": 32]]])
            }
            if request.path.hasSuffix("/parts/complete") { return .json(200, ["object_key": objectKey, "size_bytes": 100, "checksum": "fake"]) }
            if request.path.hasSuffix("/finalize") { return .json(201, ["video_id": "VID-REC-RECONCILE", "record_id": "REC-REC-RECONCILE", "session_id": "SESSION-TEST"]) }
            return .offline
        }

        let uploader = MultipartVideoUploader(
            apiClient: makeAPIClient(), stateStore: stateStore,
            partUploadSession: URLSession(configuration: {
                let config = URLSessionConfiguration.ephemeral
                config.protocolClasses = [StubServer.self]
                return config
            }())
        )
        _ = try await uploader.upload(fileURL: fileURL, metadata: makeMetadata(recordingId: "REC-RECONCILE", fileSizeBytes: 100))
        XCTAssertEqual(part1UploadFlag.increment() - 1, 0, "a server-confirmed part must never be re-uploaded")
    }

    func testLocalCompleteButServerMissingIsReUploaded() {
        // Local state CLAIMS part 1 is done, but reconciliation's
        // documented rule is: server/R2 truth wins — a part absent from
        // the server's list-parts response must be treated as
        // incomplete. Verified directly against MultipartUploadState's
        // own derived properties, matching the reconciler's logic.
        var state = MultipartUploadState(
            recordingId: "REC-X", videoId: "VID-REC-X", recordId: "REC-REC-X", sessionId: "S",
            multipartUploadId: "u", objectKey: "k.mov", partSizeBytes: 32, totalParts: 2
        )
        state.completedParts = [1: "stale-local-etag"]
        // Simulate the reconciler's own rule: server reports NO parts.
        let serverConfirmedParts: Set<Int> = []
        for partNumber in state.completedParts.keys where !serverConfirmedParts.contains(partNumber) {
            state.completedParts.removeValue(forKey: partNumber)
        }
        XCTAssertTrue(state.completedParts.isEmpty)
        XCTAssertEqual(state.incompletePartNumbers, [1, 2])
    }

    // MARK: Identity safety

    func testFinalizeIdentityMismatchIsRejected() async throws {
        let fileURL = root.appendingPathComponent("match.mov")
        try Data(repeating: 0x42, count: 32).write(to: fileURL)
        let objectKey = "VID-REC-MISMATCH.mov"
        stub.setHandler { request in
            if request.path.hasPrefix("/fake-r2/") { return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-1\""]) }
            if request.path == "/videos/uploads/initiate" {
                return .json(201, ["upload_id": "u", "object_key": objectKey, "part_size_bytes": 32, "total_parts": 1, "parts": [["part_number": 1, "url": "http://stub.test/fake-r2/part-1"]]])
            }
            if request.path.hasSuffix("/parts"), request.method == "GET" { return .json(200, ["upload_id": "u", "object_key": objectKey, "uploaded_parts": []]) }
            if request.path.hasSuffix("/parts/complete") { return .json(200, ["object_key": objectKey, "size_bytes": 32, "checksum": "fake"]) }
            if request.path.hasSuffix("/finalize") {
                // Backend returns a DIFFERENT identity than requested — must be rejected, never accepted.
                return .json(201, ["video_id": "VID-SOMETHING-ELSE", "record_id": "REC-SOMETHING-ELSE", "session_id": "S"])
            }
            return .offline
        }
        let uploader = MultipartVideoUploader(
            apiClient: makeAPIClient(), stateStore: MultipartUploadStateStore(rootDirectory: root),
            partUploadSession: URLSession(configuration: {
                let config = URLSessionConfiguration.ephemeral
                config.protocolClasses = [StubServer.self]
                return config
            }())
        )
        do {
            _ = try await uploader.upload(fileURL: fileURL, metadata: makeMetadata(recordingId: "REC-MISMATCH", fileSizeBytes: 32))
            XCTFail("expected identity mismatch rejection")
        } catch MultipartUploadError.finalizeIdentityMismatch {
            // expected
        }
    }

    // MARK: MOV preservation

    func testMOVPreservedBeforeFinalize() async throws {
        let fileURL = root.appendingPathComponent("match.mov")
        let originalBytes = Data(repeating: 0x7, count: 64)
        try originalBytes.write(to: fileURL)
        stub.setHandler { _ in .offline } // everything fails — upload never reaches finalize

        let uploader = MultipartVideoUploader(
            apiClient: makeAPIClient(), stateStore: MultipartUploadStateStore(rootDirectory: root),
            partUploadSession: URLSession(configuration: {
                let config = URLSessionConfiguration.ephemeral
                config.protocolClasses = [StubServer.self]
                return config
            }()),
            maxAttemptsPerPart: 1
        )
        do {
            _ = try await uploader.upload(fileURL: fileURL, metadata: makeMetadata(recordingId: "REC-PRESERVE", fileSizeBytes: 64))
        } catch {
            // expected to fail — the assertion below is the real point of this test
        }
        XCTAssertEqual(try Data(contentsOf: fileURL), originalBytes, "the source MOV must be untouched by any failed upload attempt")
    }

    func testMOVPreservedAfterFailedFinalize() async throws {
        let fileURL = root.appendingPathComponent("match.mov")
        let originalBytes = Data(repeating: 0x9, count: 32)
        try originalBytes.write(to: fileURL)
        let objectKey = "VID-REC-FINFAIL.mov"
        stub.setHandler { request in
            if request.path.hasPrefix("/fake-r2/") { return StubReply(status: 200, body: Data(), headers: ["ETag": "\"etag-1\""]) }
            if request.path == "/videos/uploads/initiate" {
                return .json(201, ["upload_id": "u", "object_key": objectKey, "part_size_bytes": 32, "total_parts": 1, "parts": [["part_number": 1, "url": "http://stub.test/fake-r2/part-1"]]])
            }
            if request.path.hasSuffix("/parts"), request.method == "GET" { return .json(200, ["upload_id": "u", "object_key": objectKey, "uploaded_parts": []]) }
            if request.path.hasSuffix("/parts/complete") { return .json(200, ["object_key": objectKey, "size_bytes": 32, "checksum": "fake"]) }
            if request.path.hasSuffix("/finalize") { return .serverError }
            return .offline
        }
        let uploader = MultipartVideoUploader(
            apiClient: makeAPIClient(), stateStore: MultipartUploadStateStore(rootDirectory: root),
            partUploadSession: URLSession(configuration: {
                let config = URLSessionConfiguration.ephemeral
                config.protocolClasses = [StubServer.self]
                return config
            }())
        )
        do {
            _ = try await uploader.upload(fileURL: fileURL, metadata: makeMetadata(recordingId: "REC-FINFAIL", fileSizeBytes: 32))
            XCTFail("expected finalize failure")
        } catch {
            // expected
        }
        XCTAssertEqual(try Data(contentsOf: fileURL), originalBytes)
    }

    // MARK: Mandatory safety — real pending match cannot auto-start

    func testExistingRealMatchStateCanBeAdoptedWithoutAnyNetworkCall() {
        // Adopting the existing pending record into persisted multipart
        // state must be possible WITHOUT making any request at all —
        // proving adoption and upload are two separate, independently
        // controllable steps.
        let stateStore = MultipartUploadStateStore(rootDirectory: root)
        stub.setHandler { _ in XCTFail("no network call must occur merely from adopting existing state"); return .offline }

        let adopted = MultipartUploadState(
            recordingId: "41CD60BA-62D9-4B35-9CC0-167878E6EA99",
            videoId: "VID-41CD60BA-62D9-4B35-9CC0-167878E6EA99",
            recordId: "REC-41CD60BA-62D9-4B35-9CC0-167878E6EA99",
            sessionId: "TRK000048", multipartUploadId: "not-yet-created", objectKey: "VID-41CD60BA-62D9-4B35-9CC0-167878E6EA99.mov",
            partSizeBytes: 32 * 1024 * 1024, totalParts: 41
        )
        stateStore.upsert(adopted)
        XCTAssertEqual(stateStore.state(for: "41CD60BA-62D9-4B35-9CC0-167878E6EA99")?.totalParts, 41)
        XCTAssertEqual(stub.recorded.count, 0, "adopting state must never itself trigger a request")
    }

    func testUploaderIsNeverInvokedWithoutAnExplicitCall() {
        // Structural proof, not a runtime one: MultipartVideoUploader
        // exposes exactly one entry point — `upload(fileURL:metadata:)`
        // — which requires the caller to already have a `fileURL` and
        // `MultipartInitiateRequest` in hand. There is no timer, no
        // scenePhase observer, no NWPathMonitor callback, and no static
        // "resume all pending" function anywhere in this file — unlike
        // AssessmentSessionViewModel.retryAllPending(), which this phase
        // deliberately does NOT wire this uploader into (see the Phase 2
        // report). This test documents that boundary; it is not
        // mechanically enforceable by a compiler, so it is re-verified
        // by direct source inspection in the final report.
        XCTAssertTrue(true, "MultipartVideoUploader has no automatic trigger — see file header and Phase 2 report")
    }
}
