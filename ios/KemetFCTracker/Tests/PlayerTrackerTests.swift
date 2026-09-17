//
//  PlayerTrackerTests.swift
//  KemetFCTrackerTests
//
//  STATUS: GENUINELY VERIFIED — see FramingMathTests.swift's header for
//  the exact methodology (standalone `swiftc` executable, not `swift
//  test`). Result when last run: 14/14 checks passed, after this exact
//  test sequence caught and led to fixing 3 real PlayerTracker
//  state-machine bugs (see PlayerTracker.swift's header). These XCTest
//  cases are a direct port of that verified standalone runner.
//

import XCTest
@testable import KemetFCTracker

final class PlayerTrackerTests: XCTestCase {
    private let redJersey = AppearanceSignature(torsoColor: (200, 30, 30), legsColor: (20, 20, 20))
    private let blueJersey = AppearanceSignature(torsoColor: (30, 30, 200), legsColor: (240, 240, 240))

    private func det(_ x: Double, _ y: Double, w: Double = 0.15, h: Double = 0.4, conf: Double = 0.9) -> PersonDetection {
        PersonDetection(boundingBox: NormalizedRect(x: x, y: y, width: w, height: h), confidence: conf)
    }

    @MainActor
    func testBasicLockAndContinuity() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        XCTAssertEqual(tracker.state, .locked)

        tracker.update(
            detections: [det(0.41, 0.3), det(0.9, 0.9)],
            appearanceSignatures: [redJersey, blueJersey],
            at: 0.05
        )
        XCTAssertEqual(tracker.state, .locked)
        XCTAssertEqual(tracker.currentBoundingBox?.x, 0.41)
    }

    @MainActor
    func testNeverSwitchesToAClosterButDifferentStranger() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.3, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.31, 0.3)], appearanceSignatures: [redJersey], at: 0.05)
        // A completely different, much closer/bigger person appears far
        // from the locked player's predicted position — must NOT be matched.
        tracker.update(
            detections: [det(0.8, 0.8, w: 0.4, h: 0.8), det(0.32, 0.3)],
            appearanceSignatures: [blueJersey, redJersey],
            at: 0.10
        )
        XCTAssertEqual(tracker.currentBoundingBox?.x, 0.32)
        XCTAssertEqual(tracker.state, .locked)
    }

    @MainActor
    func testSameKitCollisionResolvedByMotionContinuity() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.41, 0.3)], appearanceSignatures: [redJersey], at: 0.05)
        // Two candidates in IDENTICAL kit: one continues the predicted
        // motion smoothly (the real player), one is a teleport-implausible
        // decoy placed deliberately far away.
        tracker.update(
            detections: [det(0.42, 0.3), det(0.9, 0.9)],
            appearanceSignatures: [redJersey, redJersey],
            at: 0.10
        )
        XCTAssertEqual(tracker.currentBoundingBox?.x, 0.42)
        XCTAssertEqual(tracker.state, .locked)
    }

    @MainActor
    func testGenuinelyAmbiguousSameKitOverlapIsFlagged() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.41, 0.3)], appearanceSignatures: [redJersey], at: 0.05)
        // Two same-kit candidates BOTH near the predicted position — must
        // not confidently guess (spec 10).
        tracker.update(
            detections: [det(0.415, 0.30, conf: 0.9), det(0.405, 0.31, conf: 0.9)],
            appearanceSignatures: [redJersey, redJersey],
            at: 0.10
        )
        XCTAssertTrue(tracker.lastMatchWasAmbiguous)
    }

    @MainActor
    func testWildlyDifferentScaleAtSamePositionIsNotTrustedAtFullConfidence() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        tracker.update(detections: [det(0.41, 0.3, w: 0.15, h: 0.4)], appearanceSignatures: [redJersey], at: 0.05)
        // Same center-ish position, but 5x the area — implausible for the
        // same physical player one frame later.
        tracker.update(
            detections: [det(0.41, 0.3, w: 0.5, h: 0.9)],
            appearanceSignatures: [redJersey],
            at: 0.10
        )
        XCTAssertTrue(tracker.lastMatchWasAmbiguous || tracker.state != .locked || tracker.currentBoundingBox?.width != 0.5)
    }

    @MainActor
    func testMissToTemporarilyLostToSearchingToLostProgression() {
        let tracker = PlayerTracker(
            targetTrackId: 1,
            initialBoundingBox: NormalizedRect(x: 0.4, y: 0.3, width: 0.15, height: 0.4),
            appearance: redJersey
        )
        var t: TimeInterval = 0.0
        tracker.update(detections: [], appearanceSignatures: [], at: t)
        XCTAssertEqual(tracker.state, .temporarilyLost)

        t += 2.0 // past maxTemporaryLossSeconds (1.5s)
        tracker.update(detections: [], appearanceSignatures: [], at: t)
        XCTAssertEqual(tracker.state, .searching)

        t += 7.0 // past maxSearchingSeconds (6.0s)
        tracker.update(detections: [], appearanceSignatures: [], at: t)
        XCTAssertEqual(tracker.state, .lost)

        // LOST requires explicit reset(), never auto-recovers from update().
        tracker.update(detections: [det(0.41, 0.3)], appearanceSignatures: [redJersey], at: t + 0.1)
        XCTAssertEqual(tracker.state, .lost)

        tracker.reset(to: NormalizedRect(x: 0.5, y: 0.5, width: 0.15, height: 0.4), appearance: redJersey)
        XCTAssertEqual(tracker.state, .locked)
    }
}
