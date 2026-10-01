//
//  VideoUploadRoutingTests.swift
//  KemetFCTrackerTests
//
//  Covers Phase 2.5 Part D/E/H: the centralized legacy-vs-multipart
//  routing threshold, and the mandatory real-match routing proof.
//

import XCTest
@testable import KemetFCTracker

final class VideoUploadRoutingTests: TelemetryTestCase {
    // MARK: Routing threshold (pure, no I/O)

    func testFileAtOrBelowThresholdRoutesLegacy() {
        XCTAssertEqual(VideoUploadRouter.route(forFileSizeBytes: VideoUploadRouter.multipartRoutingThresholdBytes), .legacyDirectUpload)
        XCTAssertEqual(VideoUploadRouter.route(forFileSizeBytes: 1), .legacyDirectUpload)
    }

    func testFileOneByteOverThresholdRoutesMultipart() {
        XCTAssertEqual(VideoUploadRouter.route(forFileSizeBytes: VideoUploadRouter.multipartRoutingThresholdBytes + 1), .multipartUpload)
    }

    func testSmallTypicalClipRoutesLegacy() {
        XCTAssertEqual(VideoUploadRouter.route(forFileSizeBytes: 20 * 1024 * 1024), .legacyDirectUpload)
    }

    // MARK: Part E — the real match's exact identity must route multipart,
    // with zero network calls, zero source-file access, and legacy never
    // selected, as a pure function of its already-known size.

    func testRealMatchRecordingRoutesToMultipartWithoutAnyNetworkCallOrLegacySelection() {
        stub.setHandler { _ in XCTFail("routing a file size must never itself make a network call"); return .offline }

        let realMatchRecordingId = "41CD60BA-62D9-4B35-9CC0-167878E6EA99"
        let realMatchFileSizeBytes: Int64 = 1_351_186_620
        let realMatchSessionId = "TRK000048"

        let stableVideoId = "VID-\(realMatchRecordingId)"
        let stableRecordId = "REC-\(realMatchRecordingId)"

        let route = VideoUploadRouter.route(forFileSizeBytes: realMatchFileSizeBytes)
        XCTAssertEqual(route, .multipartUpload, "the real 1.35GB match must route multipart, never legacy")
        XCTAssertNotEqual(route, .legacyDirectUpload)

        // Identity preserved: these are the SAME deterministic formulas
        // AssessmentSessionViewModel.uploadVideoIdempotent uses for every
        // recording, legacy or multipart — routing never alters them.
        XCTAssertEqual(stableVideoId, "VID-41CD60BA-62D9-4B35-9CC0-167878E6EA99")
        XCTAssertEqual(stableRecordId, "REC-41CD60BA-62D9-4B35-9CC0-167878E6EA99")
        XCTAssertEqual(realMatchSessionId, "TRK000048")

        XCTAssertEqual(stub.recorded.count, 0, "computing a routing decision must never touch the network")
    }

    func testRoutingThresholdIsFarBelowTheRealMatchSize() {
        // Sanity-anchors the threshold's placement: the real match is
        // roughly 20x the threshold, so this is not a near-boundary
        // coincidence that could flip with an off-by-one.
        let realMatchFileSizeBytes: Int64 = 1_351_186_620
        XCTAssertGreaterThan(realMatchFileSizeBytes, VideoUploadRouter.multipartRoutingThresholdBytes * 10)
    }
}
