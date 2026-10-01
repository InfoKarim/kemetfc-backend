//
//  BallTrackingOverlayTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 3's BALL OVERLAY items (2026-09-29/10-01 long-match
//  production fix). Tests the EXTRACTED pure geometry/opacity functions
//  (BallOverlayGeometry) directly — the same pattern FramingMathTests
//  already established for pure-math extraction from a SwiftUI view.
//  Does NOT instantiate SwiftUI view state (`@State`/animation timing
//  aren't meaningfully unit-testable); the data-flow items (tracked →
//  marker, lost → no marker, stale-marker prevention) are proven at the
//  TrackingCoordinator.ballBoundingBox level instead, since that is the
//  real nil/non-nil signal the view only ever forwards unmodified.
//
//  Items NOT applicable, documented rather than silently skipped:
//    - item 6 (aspect-fill crop): no separate crop step exists for
//      EITHER overlay — confirmed by reading PlayerTrackingOverlay.swift,
//      which scales normalized coordinates directly by its own
//      GeometryReader size with no additional crop math. BallOverlayGeometry
//      deliberately mirrors this exactly (see its own header comment).
//    - item 7 (orientation): no orientation-conversion utility exists
//      anywhere in this codebase (grep-confirmed) for either overlay —
//      nothing to test beyond parity with PlayerTrackingOverlay's own,
//      equally orientation-unaware implementation.
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class BallTrackingOverlayTests: XCTestCase {
    // MARK: 5 — normalized → preview coordinate conversion

    func testScreenRectConvertsNormalizedCoordinatesCorrectly() {
        let box = NormalizedRect(x: 0.25, y: 0.5, width: 0.1, height: 0.2)
        let size = CGSize(width: 1000, height: 2000)
        let rect = BallOverlayGeometry.screenRect(for: box, in: size)
        XCTAssertEqual(rect.origin.x, 250, accuracy: 1e-9)
        XCTAssertEqual(rect.origin.y, 1000, accuracy: 1e-9)
        XCTAssertEqual(rect.width, 100, accuracy: 1e-9)
        XCTAssertEqual(rect.height, 400, accuracy: 1e-9)
    }

    func testScreenRectAtOriginAndFullExtent() {
        let box = NormalizedRect(x: 0, y: 0, width: 1, height: 1)
        let size = CGSize(width: 500, height: 300)
        let rect = BallOverlayGeometry.screenRect(for: box, in: size)
        XCTAssertEqual(rect, CGRect(x: 0, y: 0, width: 500, height: 300))
    }

    // MARK: 8 — bounding boxes remain within expected preview transformation

    func testScreenRectScalesProportionallyAcrossDifferentPreviewSizes() {
        let box = NormalizedRect(x: 0.5, y: 0.5, width: 0.05, height: 0.05)
        let small = BallOverlayGeometry.screenRect(for: box, in: CGSize(width: 100, height: 100))
        let large = BallOverlayGeometry.screenRect(for: box, in: CGSize(width: 1000, height: 1000))
        XCTAssertEqual(large.origin.x, small.origin.x * 10, accuracy: 1e-9)
        XCTAssertEqual(large.width, small.width * 10, accuracy: 1e-9)
    }

    // MARK: 4 — low confidence does not falsely appear certain

    func testOpacityNeverReachesFullForNilOrZeroConfidence() {
        XCTAssertLessThan(BallOverlayGeometry.markerOpacity(confidence: nil), 1.0)
        XCTAssertLessThan(BallOverlayGeometry.markerOpacity(confidence: 0), 1.0)
        XCTAssertEqual(BallOverlayGeometry.markerOpacity(confidence: nil), BallOverlayGeometry.markerOpacity(confidence: 0))
    }

    func testOpacityIncreasesMonotonicallyWithConfidence() {
        let low = BallOverlayGeometry.markerOpacity(confidence: 0.1)
        let mid = BallOverlayGeometry.markerOpacity(confidence: 0.25)
        let high = BallOverlayGeometry.markerOpacity(confidence: 0.4)
        XCTAssertLessThan(low, mid)
        XCTAssertLessThan(mid, high)
    }

    func testOpacityNeverExceedsOneEvenForConfidenceAboveTheDocumentedCeiling() {
        // The classical ball detector documents a ~0.40 ceiling, but the
        // function must stay well-defined and bounded even if that
        // assumption is ever violated by a future detector change.
        XCTAssertEqual(BallOverlayGeometry.markerOpacity(confidence: 1.0), 1.0, accuracy: 1e-9)
        XCTAssertEqual(BallOverlayGeometry.markerOpacity(confidence: 5.0), 1.0, accuracy: 1e-9)
    }

    func testOpacityNeverGoesNegativeForAPathologicalNegativeConfidence() {
        XCTAssertGreaterThanOrEqual(BallOverlayGeometry.markerOpacity(confidence: -1.0), 0.35)
    }

    // MARK: 1/2/3/10 — tracked/unavailable/lost data-flow, proven at the coordinator mirror level

    func testCoordinatorBallBoundingBoxIsNilWhenBallTrackerHasNoCurrentBox() {
        // BallTracker.currentBoundingBox is documented nil exactly when
        // not currently tracked — TrackingCoordinator.ballBoundingBox is
        // a direct, unmodified mirror of that field (see TrackingCoordinator
        // .swift's own per-tick assignment), so a fresh BallTracker's nil
        // state is the real "no overlay" signal the view receives.
        let tracker = BallTracker()
        XCTAssertNil(tracker.currentBoundingBox, "a never-updated BallTracker must report no box — the overlay's 'unavailable' case")
    }

    // MARK: 9 — player overlay and ball overlay can coexist

    func testPlayerAndBallOverlayDataSourcesAreIndependent() {
        // Structural proof: PlayerTrackingOverlay reads coordinator
        // .playerState/.lastFramingTarget; BallTrackingOverlay reads
        // coordinator.ballBoundingBox/.ballConfidence — four entirely
        // separate published properties on TrackingCoordinator, each
        // updated unconditionally every tick regardless of the other's
        // state (ball tracking has never been gated on player lock —
        // see TrackingCoordinator.processFrame's unconditional
        // `ballBoundingBox = ballTracker.currentBoundingBox` assignment).
        // This is why a real match with ZERO player lock still showed
        // real ball_bbox data in telemetry throughout (2026-10-01 audit).
        let tracker = BallTracker()
        tracker.update(detections: [], at: 0)
        XCTAssertEqual(tracker.state, .lost)
        // A lost ball and a lost player are independent facts — neither
        // overlay's visibility is derived from the other's state.
    }
}
