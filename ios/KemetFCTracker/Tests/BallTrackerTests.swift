//
//  BallTrackerTests.swift
//  KemetFCTrackerTests
//
//  Deterministic ball-tracking semantics. NONE of this makes the classical
//  detector a production ball detector — it only proves the tracker does not
//  fabricate, confuse or mislabel what it is given.
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class BallTrackerTests: XCTestCase {
    private let dt = 1.0 / 15.0

    private func ball(_ x: Double, _ y: Double = 0.5, conf: Double = 0.35, size: Double = 0.03) -> BallDetection {
        BallDetection(boundingBox: NormalizedRect(x: x - size / 2, y: y - size / 2, width: size, height: size), confidence: conf)
    }

    /// Confirms a track at (x, 0.5): two consecutive detections. Returns the time of the last one.
    private func acquire(_ tracker: BallTracker, at x: Double, from start: Double = 0) -> Double {
        tracker.update(detections: [ball(x)], at: start + dt)
        tracker.update(detections: [ball(x + 0.01)], at: start + 2 * dt)
        return start + 2 * dt
    }

    func testNoBallFrameMeansNotDetected() {
        let tracker = BallTracker()
        tracker.update(detections: [], at: dt)
        XCTAssertNil(tracker.currentBoundingBox)
        XCTAssertNil(tracker.currentTrackId)
        XCTAssertEqual(tracker.currentConfidence, 0)
        XCTAssertEqual(tracker.state, .lost)
    }

    func testOneFrameFlickerNeverBecomesABall() {
        let tracker = BallTracker()
        tracker.update(detections: [ball(0.5)], at: dt)
        XCTAssertNil(tracker.currentBoundingBox, "a single detection is not yet a ball")
        XCTAssertNil(tracker.currentTrackId)
        tracker.update(detections: [], at: 2 * dt)
        tracker.update(detections: [ball(0.5)], at: 3 * dt)     // the flicker is not consecutive
        XCTAssertNil(tracker.currentBoundingBox)
        XCTAssertEqual(tracker.acquiredTrackCount, 0)
    }

    func testContinuousMotionIsFollowedWithOneStableTrackId() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        XCTAssertEqual(tracker.state, .tracked)
        XCTAssertEqual(tracker.currentTrackId, 1)
        for step in 1...20 {
            time += dt
            let x = 0.21 + 0.02 * Double(step)
            tracker.update(detections: [ball(x)], at: time)
            XCTAssertEqual(tracker.state, .tracked)
            XCTAssertEqual(tracker.currentTrackId, 1, "the same ball keeps the same identity")
            XCTAssertEqual(tracker.currentBoundingBox?.center.x ?? 0, x, accuracy: 1e-9)
            XCTAssertEqual(tracker.currentConfidence, 0.35, "confidence is the detector's, unaltered")
        }
    }

    func testTemporaryDisappearanceThenReappearanceKeepsTheSameTrack() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        for step in 1...3 {                      // three frames with no ball
            time += dt
            tracker.update(detections: [], at: time)
            XCTAssertEqual(tracker.state, .searching)
            XCTAssertNil(tracker.currentBoundingBox, "NOT detected: no stale box")
            XCTAssertNil(tracker.currentTrackId)
            XCTAssertNotNil(tracker.lastKnownBoundingBox, "kept only for framing continuity")
            _ = step
        }
        time += dt
        tracker.update(detections: [ball(0.21 + 0.02 * 4)], at: time)     // back on its path
        XCTAssertEqual(tracker.state, .tracked)
        XCTAssertEqual(tracker.currentTrackId, 1)
    }

    func testImpossibleJumpIsRejectedNotFollowed() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        time += dt
        tracker.update(detections: [ball(0.85)], at: time)       // 0.64 in one frame: ~9.6 units/s
        XCTAssertNil(tracker.currentBoundingBox, "a physically implausible jump is not the ball")
        XCTAssertEqual(tracker.state, .searching)
        time += dt
        tracker.update(detections: [ball(0.24)], at: time)       // the real ball, still on its path
        XCTAssertEqual(tracker.state, .tracked)
        XCTAssertEqual(tracker.currentTrackId, 1)
    }

    func testCompetingCircularObjectsThatMotionCannotSeparateYieldNoDetection() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        time += dt
        // Two look-alikes equally close to where the ball should be.
        tracker.update(detections: [ball(0.215), ball(0.225)], at: time)
        XCTAssertNil(tracker.currentBoundingBox)
        XCTAssertEqual(tracker.state, .searching)
    }

    func testClearlyNearestCandidateWinsOverAFarDecoy() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        time += dt
        tracker.update(detections: [ball(0.70, 0.5, conf: 0.40), ball(0.22)], at: time)
        XCTAssertEqual(tracker.currentBoundingBox?.center.x ?? 0, 0.22, accuracy: 1e-9)
        XCTAssertEqual(tracker.currentTrackId, 1)
    }

    func testStationaryFalsePositiveBesideAMovingBallIsNeverAdopted() {
        let tracker = BallTracker()
        let decoy = ball(0.80, 0.80, conf: 0.40)
        var time = acquire(tracker, at: 0.10)
        // 15 frames: the real ball moves, a bright cone sits still. The tracker follows the ball.
        for step in 1...15 {
            time += dt
            tracker.update(detections: [ball(0.11 + 0.02 * Double(step)), decoy], at: time)
            XCTAssertEqual(tracker.currentBoundingBox?.center.x ?? 0, 0.11 + 0.02 * Double(step), accuracy: 1e-9)
            XCTAssertEqual(tracker.currentTrackId, 1)
        }
        // The ball drops out; only the cone remains, in view frame after frame.
        for _ in 1...45 {                                        // 3 s: longer than the search window
            time += dt
            tracker.update(detections: [decoy], at: time)
            XCTAssertNil(tracker.currentBoundingBox, "the cone must not become the ball")
            XCTAssertNil(tracker.currentTrackId)
        }
        XCTAssertEqual(tracker.state, .lost)
    }

    func testReappearanceAfterALongLossStartsANewTrackWithANewId() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        XCTAssertEqual(tracker.currentTrackId, 1)
        for _ in 1...40 { time += dt; tracker.update(detections: [], at: time) }      // ~2.7 s without a ball
        XCTAssertEqual(tracker.state, .lost)
        XCTAssertNil(tracker.lastKnownBoundingBox)

        time += dt
        tracker.update(detections: [ball(0.7)], at: time)
        XCTAssertNil(tracker.currentTrackId, "needs confirmation again")
        time += dt
        tracker.update(detections: [ball(0.71)], at: time)
        XCTAssertEqual(tracker.state, .tracked)
        XCTAssertEqual(tracker.currentTrackId, 2, "a new track after a loss must not inherit the old identity")
    }

    func testLowConfidenceDetectionsAreIgnored() {
        let tracker = BallTracker()
        tracker.update(detections: [ball(0.5, conf: 0.05)], at: dt)
        tracker.update(detections: [ball(0.5, conf: 0.05)], at: 2 * dt)
        XCTAssertNil(tracker.currentBoundingBox)
        XCTAssertEqual(tracker.acquiredTrackCount, 0)
    }

    func testSeveralEquallyConvincingCandidatesAtAcquisitionAreNotGuessed() {
        let tracker = BallTracker()
        for frame in 1...4 {
            tracker.update(detections: [ball(0.3, conf: 0.35), ball(0.7, conf: 0.36)], at: Double(frame) * dt)
        }
        XCTAssertNil(tracker.currentBoundingBox)
        XCTAssertEqual(tracker.acquiredTrackCount, 0, "no motion history and no clear favourite: report nothing")
    }

    func testAClearlyMoreConfidentCandidateIsAcquired() {
        let tracker = BallTracker()
        tracker.update(detections: [ball(0.3, conf: 0.40), ball(0.7, conf: 0.22)], at: dt)
        tracker.update(detections: [ball(0.31, conf: 0.40), ball(0.7, conf: 0.22)], at: 2 * dt)
        XCTAssertEqual(tracker.currentBoundingBox?.center.x ?? 0, 0.31, accuracy: 1e-9)
        XCTAssertEqual(tracker.currentTrackId, 1)
    }

    func testHeldBoundingBoxIsOnlyForBriefFramingContinuity() {
        let tracker = BallTracker()
        let time = acquire(tracker, at: 0.20)
        tracker.update(detections: [], at: time + dt)
        XCTAssertNotNil(tracker.heldBoundingBox(at: time + dt))
        XCTAssertNil(tracker.heldBoundingBox(at: time + 1.0), "an old position is not held for framing")
    }

    // MARK: - Track id reaches telemetry (not a hard-coded 1)

    func testTrackIdPropagatesIntoTheFrameTelemetry() {
        let tracker = BallTracker()
        var time = acquire(tracker, at: 0.20)
        func frame() -> FrameTelemetry {
            FrameTelemetryAssembler.assemble(
                playerState: .locked, playerBox: NormalizedRect(x: 0.2, y: 0.3, width: 0.1, height: 0.4), playerConfidence: 0.9,
                playerTargetTrackId: 1, playerMatchWasAmbiguous: false,
                ballBox: tracker.currentBoundingBox, ballConfidence: tracker.currentConfidence, ballTrackId: tracker.currentTrackId,
                poses: [], personBoxes: [], mode: .smartSoccer
            )
        }
        XCTAssertEqual(frame().ballTrackId, 1)
        for _ in 1...40 { time += dt; tracker.update(detections: [], at: time) }
        XCTAssertNil(frame().ballTrackId, "not detected: unknown, not 1")
        tracker.update(detections: [ball(0.6)], at: time + dt)
        tracker.update(detections: [ball(0.61)], at: time + 2 * dt)
        XCTAssertEqual(frame().ballTrackId, 2, "the id changes with the track")
    }
}
