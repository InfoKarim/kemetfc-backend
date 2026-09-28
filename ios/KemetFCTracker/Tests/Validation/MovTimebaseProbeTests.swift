//
//  MovTimebaseProbeTests.swift
//  KemetFCTrackerTests
//
//  Proves the PURE comparison/derived-field logic in MovTimebaseProbe.swift
//  and MovTimebaseComparator.swift. The AVAssetReader-driving async function
//  `MovTimebaseProbe.inspect(url:)` itself is verified separately via a
//  one-off manual smoke test against a real, existing .mov (see the phase
//  report) rather than a committed fixture — the only local .mov files
//  found in this repository live under `uploads/videos/` (the BACKEND's own
//  upload storage, real platform data, outside `ios/` entirely) and are
//  deliberately NOT wired into the Xcode test target as a hard dependency:
//  that path is not portable (would not exist on another machine/CI), and
//  is not appropriate to commit as a test fixture.
//

import XCTest
@testable import KemetFCTracker

final class MovTimebaseProbeTests: XCTestCase {
    private func capture(pts: Double?, videoFilename: String = "clip.mov", provenance: SynchronizationProvenance = .unavailable) -> TouchCandidateCaptureExport {
        let sync: TimebaseSynchronizationRecord? = pts.map {
            TimebaseSynchronizationRecord(
                assessmentId: "A1", analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
                capturePtsOriginSeconds: $0, offsetSeconds: nil, provenance: provenance
            )
        }
        return TouchCandidateCaptureExport(
            assessmentId: "A1", sessionId: nil, video: VideoReference(filename: videoFilename),
            predictionTimebase: .analysisSeconds, synchronization: sync, predictions: []
        )
    }

    private func probeResult(filePath: String = "/x/clip.mov", firstPts: Double?) -> MovTimebaseProbeResult {
        MovTimebaseProbeResult(
            filePath: filePath, assetDurationSeconds: 60, videoTrackCount: 1,
            firstVideoSamplePtsSeconds: firstPts, lastVideoSamplePtsSeconds: firstPts.map { $0 + 59 },
            firstSampleDurationSeconds: 1.0 / 30.0, trackNaturalTimeScale: 30000
        )
    }

    // MARK: Derived fields on MovTimebaseProbeResult

    func testFirstPtsApproximatelyZeroTrueWhenNearZero() {
        XCTAssertEqual(probeResult(firstPts: 0.0003).firstPtsIsApproximatelyZero, true)
    }

    func testFirstPtsApproximatelyZeroFalseWhenLarge() {
        XCTAssertEqual(probeResult(firstPts: 1234.5).firstPtsIsApproximatelyZero, false)
    }

    func testFirstPtsFieldsNilWhenNoVideoTrack() {
        let result = MovTimebaseProbeResult(filePath: "/x/empty.mov", assetDurationSeconds: nil, videoTrackCount: 0, firstVideoSamplePtsSeconds: nil, lastVideoSamplePtsSeconds: nil, firstSampleDurationSeconds: nil, trackNaturalTimeScale: nil)
        XCTAssertNil(result.firstPtsIsApproximatelyZero, "must be nil, never a fabricated false, when there is nothing to observe")
        XCTAssertNil(result.appearsNormalizedToFileRelativeTimeline)
    }

    // MARK: 1-3 — availability cases

    func testCapturePtsAbsentYieldsMovOnly() {
        let outcome = MovTimebaseComparator.compare(capture: nil, movProbe: probeResult(firstPts: 5.0))
        XCTAssertEqual(outcome, .movOnly(firstMovVideoSamplePtsSeconds: 5.0))
    }

    func testMovPtsAbsentYieldsCaptureOnly() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: 3.0), movProbe: nil)
        XCTAssertEqual(outcome, .captureOnly(capturePtsOriginSeconds: 3.0))
    }

    func testBothPresentYieldsCompared() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: 3.0, videoFilename: "clip.mov"), movProbe: probeResult(filePath: "/x/clip.mov", firstPts: 5.0))
        guard case .compared(let comparison) = outcome else { return XCTFail("expected .compared, got \(outcome)") }
        XCTAssertEqual(comparison.capturePtsOriginSeconds, 3.0)
        XCTAssertEqual(comparison.firstMovVideoSamplePtsSeconds, 5.0)
    }

    // MARK: 4 — difference sign

    func testDifferenceSignIsMovMinusCapture() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: 100.0, videoFilename: "clip.mov"), movProbe: probeResult(filePath: "/x/clip.mov", firstPts: 100.2))
        guard case .compared(let comparison) = outcome else { return XCTFail() }
        XCTAssertEqual(comparison.differenceSeconds, 0.2, accuracy: 1e-9, "mov(100.2) - capture(100.0) = +0.2")

        let reversed = MovTimebaseComparator.compare(capture: capture(pts: 100.2, videoFilename: "clip.mov"), movProbe: probeResult(filePath: "/x/clip.mov", firstPts: 100.0))
        guard case .compared(let reversedComparison) = reversed else { return XCTFail() }
        XCTAssertEqual(reversedComparison.differenceSeconds, -0.2, accuracy: 1e-9, "mov(100.0) - capture(100.2) = -0.2")
    }

    // MARK: 5 — no fabricated zero

    func testNeitherAvailableIsItsOwnCaseNeverAZeroComparison() {
        let outcome = MovTimebaseComparator.compare(capture: nil, movProbe: nil)
        XCTAssertEqual(outcome, .neitherAvailable)
        // Also: a capture export with NO synchronization record at all
        // (the honest, unavailable state) must behave identically to nil.
        let noSyncCapture = TouchCandidateCaptureExport(assessmentId: "A1", sessionId: nil, video: VideoReference(filename: "clip.mov"), predictionTimebase: .analysisSeconds, synchronization: nil, predictions: [])
        XCTAssertEqual(MovTimebaseComparator.compare(capture: noSyncCapture, movProbe: nil), .neitherAvailable)
    }

    // MARK: 6 — non-finite rejected

    func testNonFiniteCaptureValueIsTreatedAsAbsent() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: .nan), movProbe: probeResult(firstPts: 5.0))
        XCTAssertEqual(outcome, .movOnly(firstMovVideoSamplePtsSeconds: 5.0), "NaN must never be used in a comparison")
    }

    func testNonFiniteMovValueIsTreatedAsAbsent() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: 3.0), movProbe: probeResult(firstPts: .infinity))
        XCTAssertEqual(outcome, .captureOnly(capturePtsOriginSeconds: 3.0), "infinity must never be used in a comparison")
    }

    // MARK: 7 — negative values

    func testNegativeValuesAreHandledExplicitlyNotRejected() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: -0.1, videoFilename: "clip.mov"), movProbe: probeResult(filePath: "/x/clip.mov", firstPts: -0.05))
        guard case .compared(let comparison) = outcome else { return XCTFail("a negative-but-finite value is valid data, not an error") }
        XCTAssertEqual(comparison.differenceSeconds, 0.05, accuracy: 1e-9)
    }

    // MARK: 8 — identity mismatch rejected

    func testMismatchedIdentityIsRejectedNotSilentlyCompared() {
        let outcome = MovTimebaseComparator.compare(
            capture: capture(pts: 3.0, videoFilename: "assessment-A.mov"),
            movProbe: probeResult(filePath: "/somewhere/assessment-B.mov", firstPts: 5.0)
        )
        XCTAssertEqual(outcome, .rejectedIdentityMismatch(captureVideoFilename: "assessment-A.mov", movFilename: "assessment-B.mov"))
    }

    func testMatchedIdentityProducesAConfirmedMatchFlag() {
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: 3.0, videoFilename: "same.mov"), movProbe: probeResult(filePath: "/x/same.mov", firstPts: 5.0))
        guard case .compared(let comparison) = outcome else { return XCTFail() }
        XCTAssertEqual(comparison.identityCheck, .matched)
    }

    // MARK: 9 — raw comparison never becomes measured synchronization

    func testRawComparisonNeverProducesATimestampMappingByItself() {
        // The comparator's own API surface has no path to a TimestampMapping
        // at all — proven structurally: RawTimebaseComparisonOutcome and
        // RawTimebaseComparison are the ONLY output types, neither of which
        // is (or converts implicitly to) TimestampMapping.
        let outcome = MovTimebaseComparator.compare(capture: capture(pts: 3.0, videoFilename: "clip.mov"), movProbe: probeResult(filePath: "/x/clip.mov", firstPts: 3.2))
        guard case .compared(let comparison) = outcome else { return XCTFail() }
        // A RawTimebaseComparison has no `provenance` field claiming
        // measurement — only the raw numbers and an identity check.
        XCTAssertEqual(comparison.differenceSeconds, 0.2, accuracy: 1e-9)
    }

    // MARK: 10 — historical approximation never substituted

    func testHistoricalApproximateOffsetIsNeverUsedAsASubstituteValue() {
        // If capture-side data is absent, the comparator must report
        // `.movOnly` — it must NEVER fall back to inventing ~0.15-0.2s as if
        // it were the missing capture value.
        let outcome = MovTimebaseComparator.compare(capture: nil, movProbe: probeResult(firstPts: 10.0))
        guard case .movOnly(let value) = outcome else { return XCTFail() }
        XCTAssertEqual(value, 10.0, "must be the RAW mov PTS, never adjusted by any historical approximation")
    }
}
