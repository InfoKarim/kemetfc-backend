//
//  TouchCandidateDetectorTests.swift
//  KemetFCTrackerTests
//
//  Proves TouchCandidateDetector (see the 2026-09-27 Touch Candidate audit)
//  only emits a candidate from a real, sustained ball-motion change while
//  near the player — never from a single frame, never from unavailable data,
//  never across a new-ball-track's unmeasured first velocity, never twice for
//  the same interaction episode, and never leaking across a reset. Also
//  documents, rather than hides, the camera/gimbal-motion confound this
//  low-level measurement cannot rule out.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateDetectorTests: XCTestCase {
    @discardableResult
    private func tick(
        _ detector: TouchCandidateDetector, t: TimeInterval,
        proximity: BallProximityState, ballVelocity: NormalizedPoint?,
        ballVisible: Bool = true, distance: Double? = 1.0,
        coMovementState: PlayerBallCoMovementState = .unavailable, ballTrackId: Int? = 1,
        trackingState: TrackingState = .locked
    ) -> TouchCandidateEvent? {
        let analysis = PlayerLiveAnalysisSnapshot(
            timestamp: t, trackingState: trackingState, trackingConfidence: trackingState == .locked ? 0.9 : nil,
            movementState: .uncertain, ballVisible: ballVisible, ballConfidence: ballVisible ? 0.3 : nil,
            ballProximityState: proximity, normalizedPlayerBallDistance: distance,
            ballVelocity: ballVelocity, playerVelocity: nil, relativeBallPosition: nil,
            ballTrackId: ballVisible ? ballTrackId : nil
        )
        let coMovement = PlayerBallCoMovementSnapshot(timestamp: t, state: coMovementState)
        return detector.observe(analysis: analysis, coMovement: coMovement)
    }

    /// Fills the pre-window buffer with a constant velocity while the ball is
    /// away, then arms by transitioning to near at `armTime` with the SAME
    /// velocity (so `preBallSpeed`/direction is exactly `velocity`).
    private func approachThenArm(_ detector: TouchCandidateDetector, velocity: NormalizedPoint, armTime: TimeInterval, farDistance: Double = 3.0, nearDistance: Double = 1.0) {
        for i in 0..<5 {
            let t = armTime - 0.5 + Double(i) * 0.1
            _ = tick(detector, t: t, proximity: .away, ballVelocity: velocity, distance: farDistance)
        }
        _ = tick(detector, t: armTime, proximity: .near, ballVelocity: velocity, distance: nearDistance)
    }

    // MARK: - Availability (Step 10: no ball / no player / invalid velocity / first ball-track observation)

    func testNoBallNeverArms() {
        let detector = TouchCandidateDetector()
        let result = tick(detector, t: 0, proximity: .unavailable, ballVelocity: nil, ballVisible: false, distance: nil)
        XCTAssertNil(result)
        XCTAssertNil(detector.lastRejectionReason)
    }

    func testNoPlayerNeverArmsBecauseProximityCannotBeNear() {
        // PlayerAnalysisEngine itself only ever reports `.near`/`.away` when
        // the player IS observed — an unobserved player always yields
        // `.unavailable` proximity, so TouchCandidateDetector inherits this
        // for free without checking trackingState itself.
        let detector = TouchCandidateDetector()
        for i in 0..<10 {
            _ = tick(detector, t: Double(i) * 0.1, proximity: .unavailable, ballVelocity: .init(x: 0.3, y: 0), distance: nil, trackingState: .lost)
        }
        XCTAssertTrue(detector.events.isEmpty)
    }

    func testInvalidBallVelocityAtArmingTimeYieldsInsufficientPreSamples() {
        let detector = TouchCandidateDetector()
        // Ball visible but velocity invalid (e.g. first observation) the
        // whole time leading up to, and at, near-zone entry.
        for i in 0..<5 {
            _ = tick(detector, t: Double(i) * 0.1, proximity: .away, ballVelocity: nil, distance: 3.0)
        }
        let result = tick(detector, t: 0.5, proximity: .near, ballVelocity: nil, distance: 1.0)
        XCTAssertNil(result)
        XCTAssertEqual(detector.lastRejectionReason, .insufficientPreSamples)
    }

    func testFirstBallTrackObservationNearPlayerDoesNotManufactureACandidate() {
        // A brand-new ball track's first frame reports ballVelocity == nil
        // (per PlayerAnalysisEngine's own rule) even though the ball is
        // immediately near the player — must not be treated as a real zero.
        let detector = TouchCandidateDetector()
        let arm = tick(detector, t: 0, proximity: .near, ballVelocity: nil, distance: 1.0)
        XCTAssertNil(arm)
        XCTAssertEqual(detector.lastRejectionReason, .insufficientPreSamples)
        // Even if the track then shows a "large" velocity once truly
        // measured, THIS episode already failed to arm with real evidence —
        // it must not retroactively fabricate a pre-window from nothing.
        let next = tick(detector, t: 0.1, proximity: .near, ballVelocity: .init(x: 0.5, y: 0), distance: 1.0)
        XCTAssertNil(next, "a single fresh sample is still not a valid pre-window average for a NEW episode")
    }

    // MARK: - Distance / stationary / unchanged trajectory (Step 5 scenarios 1-2, Step 10)

    func testBallFarFromPlayerNeverArms() {
        let detector = TouchCandidateDetector()
        for i in 0..<20 {
            let t = Double(i) * 0.1
            // Direction changes sharply, but the ball is never near.
            let v = i < 10 ? NormalizedPoint(x: 0.3, y: 0) : NormalizedPoint(x: -0.3, y: 0)
            _ = tick(detector, t: t, proximity: .away, ballVelocity: v, distance: 5.0)
        }
        XCTAssertTrue(detector.events.isEmpty, "a motion change far from the player must never arm an episode")
    }

    func testStationaryBallNearPlayerYieldsNoMeaningfulMotionChange() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0, y: 0), armTime: 0.5)
        var last: TouchCandidateEvent?
        for i in 1...5 {
            last = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0, y: 0), distance: 1.0)
        }
        XCTAssertNil(last)
        XCTAssertEqual(detector.lastRejectionReason, .noMeaningfulMotionChange)
    }

    func testUnchangedTrajectoryPassingNearPlayerYieldsNoCandidate() {
        let detector = TouchCandidateDetector()
        let v = NormalizedPoint(x: 0.2, y: 0)
        approachThenArm(detector, velocity: v, armTime: 0.5)
        var last: TouchCandidateEvent?
        for i in 1...5 {
            last = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: v, distance: 1.0)
        }
        XCTAssertNil(last)
        XCTAssertEqual(detector.lastRejectionReason, .noMeaningfulMotionChange)
    }

    // MARK: - Meaningful change (Step 10, Step 5 scenario 10)

    func testMeaningfulSpeedChangeNearPlayerEmitsCandidate() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        var event: TouchCandidateEvent?
        for i in 1...5 {
            event = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.35, y: 0), distance: 1.0)
        }
        guard let event else { return XCTFail("expected a candidate") }
        XCTAssertEqual(event.evidence.preBallSpeed ?? -1, 0.2, accuracy: 1e-9)
        XCTAssertEqual(event.evidence.postBallSpeed ?? -1, 0.35, accuracy: 1e-9)
        XCTAssertEqual(event.evidence.speedChange ?? -1, 0.15, accuracy: 1e-9)
        XCTAssertEqual(detector.events.count, 1)
    }

    func testMeaningfulDirectionChangeNearPlayerEmitsCandidate() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        var event: TouchCandidateEvent?
        for i in 1...5 {
            event = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: -0.2, y: 0), distance: 1.0)
        }
        guard let event else { return XCTFail("expected a candidate") }
        XCTAssertEqual(event.evidence.directionSimilarity ?? 1, -1, accuracy: 1e-9)
        XCTAssertEqual(event.evidence.speedChange ?? 1, 0, accuracy: 1e-9, "same speed — this must fire on direction alone")
    }

    func testTinyJitterBelowThresholdNeverEmitsACandidate() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        var last: TouchCandidateEvent?
        // A tiny bump, well under both the speed-change and direction
        // thresholds.
        for i in 1...5 {
            last = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.21, y: 0.01), distance: 1.0)
        }
        XCTAssertNil(last)
        XCTAssertEqual(detector.lastRejectionReason, .noMeaningfulMotionChange)
    }

    // MARK: - Dropouts / resumption (Step 5 scenarios 5-6, Step 10)

    func testTemporaryBallDropoutDuringPostWindowDoesNotAbortTheEpisode() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        // A brief, grace-tolerated dropout mid-post-window.
        _ = tick(detector, t: 0.6, proximity: .unavailable, ballVelocity: nil, ballVisible: false, distance: nil)
        var event: TouchCandidateEvent?
        for i in 2...5 {
            event = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0)
        }
        XCTAssertNotNil(event, "a brief dropout must not silently discard an otherwise-real motion change")
    }

    func testSameTrackResumeAfterDropoutContributesToTheSameEpisode() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        _ = tick(detector, t: 0.6, proximity: .unavailable, ballVelocity: nil, ballVisible: false, distance: nil)
        // Resumes with the SAME track id, valid velocity again.
        _ = tick(detector, t: 0.9, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0, ballTrackId: 1)
        let event = tick(detector, t: 1.0, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0, ballTrackId: 1)
        XCTAssertNotNil(event)
        XCTAssertEqual(event?.ballTrackId, 1)
    }

    func testGenuinelyNewBallTrackNearPlayerDoesNotManufactureATouch() {
        // Ball leaves, then genuinely disappears long enough for the
        // pre-window buffer to fully age out and the old episode's cooldown
        // to fully clear. A NEW track then appears immediately near the
        // player — its first frame reports ballVelocity == nil (mirroring
        // PlayerAnalysisEngine's own rule); the episode must never treat
        // that nil as a zero measurement, and must not reuse the OLD
        // track's stale pre-window average.
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        _ = tick(detector, t: 0.6, proximity: .away, ballVelocity: .init(x: 0.2, y: 0), distance: 3.0) // concludes, no meaningful change
        _ = tick(detector, t: 2.0, proximity: .unavailable, ballVelocity: nil, ballVisible: false, distance: nil) // genuinely gone
        let firstFrameOfNewTrack = tick(detector, t: 2.1, proximity: .near, ballVelocity: nil, distance: 1.0, ballTrackId: 2)
        XCTAssertNil(firstFrameOfNewTrack)
        XCTAssertEqual(detector.lastRejectionReason, .insufficientPreSamples)
    }

    // MARK: - Reset / session isolation

    func testResetClearsInProgressEpisodeAndHistory() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        for i in 1...5 {
            _ = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0)
        }
        XCTAssertEqual(detector.events.count, 1)

        detector.reset()
        XCTAssertTrue(detector.events.isEmpty)
        XCTAssertNil(detector.lastRejectionReason)

        // A fresh, complete episode after reset must work normally and
        // must not inherit anything from before.
        approachThenArm(detector, velocity: .init(x: 0.1, y: 0), armTime: 100.0)
        var event: TouchCandidateEvent?
        for i in 1...5 {
            event = tick(detector, t: 100.0 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.3, y: 0), distance: 1.0)
        }
        XCTAssertNotNil(event)
        XCTAssertEqual(detector.events.count, 1, "must not carry over the pre-reset event")
    }

    // MARK: - Timestamps / gaps (Step 10)

    func testIrregularTimestampsDoNotCorruptAnInProgressEpisode() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        _ = tick(detector, t: 0.5, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0) // duplicate of arm time — no-op
        _ = tick(detector, t: 0.4, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0) // backward — no-op
        var event: TouchCandidateEvent?
        for i in 1...5 {
            event = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0)
        }
        XCTAssertNotNil(event)
    }

    func testLargeObservationGapConcludesTheEpisodeRatherThanHanging() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        // A huge jump forward with no ball — the episode must conclude
        // (grace exceeded), not silently wait forever.
        let result = tick(detector, t: 10.0, proximity: .unavailable, ballVelocity: nil, ballVisible: false, distance: nil)
        XCTAssertNil(result)
        XCTAssertEqual(detector.lastRejectionReason, .insufficientPostSamples)
    }

    // MARK: - Event semantics (Step 8, Step 10)

    func testDuplicateEventPreventionWithinASingleContinuousNearEpisode() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        var events: [TouchCandidateEvent] = []
        for i in 1...20 { // well past postWindowSeconds + cooldown, still continuously near
            if let e = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0) {
                events.append(e)
            }
        }
        XCTAssertEqual(events.count, 1, "a single sustained change must not be reported repeatedly while still near")
    }

    func testRearmForASecondRealInteractionAfterLeavingAndReenteringNearZone() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        var firstEvent: TouchCandidateEvent?
        for i in 1...5 {
            firstEvent = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.4, y: 0), distance: 1.0)
        }
        XCTAssertNotNil(firstEvent)

        // Ball leaves, then genuinely returns later with a second real change.
        for i in 0..<5 {
            _ = tick(detector, t: 2.0 + Double(i) * 0.1, proximity: .away, ballVelocity: .init(x: 0.4, y: 0), distance: 3.0)
        }
        approachThenArm(detector, velocity: .init(x: 0.4, y: 0), armTime: 2.6)
        var secondEvent: TouchCandidateEvent?
        for i in 1...5 {
            secondEvent = tick(detector, t: 2.6 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.15, y: 0), distance: 1.0)
        }
        XCTAssertNotNil(secondEvent)
        XCTAssertEqual(detector.events.count, 2)
    }

    // MARK: - Co-movement interaction (Step 5 scenario 3, Step 10)

    func testSustainedCoMovementWithoutMotionChangeIsNotAutomaticallyATouch() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        var last: TouchCandidateEvent?
        for i in 1...5 {
            last = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.2, y: 0), distance: 1.0, coMovementState: .sustained)
        }
        XCTAssertNil(last, "sustained co-movement alone, with no ball motion change, must never be reported as a touch candidate")
        XCTAssertEqual(detector.lastRejectionReason, .noMeaningfulMotionChange)
    }

    func testMotionChangeWithoutProximityNeverArms() {
        let detector = TouchCandidateDetector()
        for i in 0..<10 {
            let v = i < 5 ? NormalizedPoint(x: 0.2, y: 0) : NormalizedPoint(x: 0.5, y: 0.3)
            _ = tick(detector, t: Double(i) * 0.1, proximity: .away, ballVelocity: v, distance: 4.0)
        }
        XCTAssertTrue(detector.events.isEmpty)
    }

    // MARK: - Known, documented, unfixed limitation (Step 6, Step 10)

    /// The ball's velocity is raw image-space, uncompensated for camera
    /// motion — if the gimbal's own correction behavior differs between the
    /// pre- and post-windows (e.g. it starts correcting harder as the ball
    /// nears the player), that alone produces the SAME apparent speed/
    /// direction change as a real touch, and this detector cannot tell them
    /// apart. This test documents that limitation as a visible regression
    /// (asserting the honestly-limited "candidate fires" outcome), per Step 6
    /// — it must NOT be "fixed" by tuning thresholds; only real gimbal-
    /// orientation telemetry (out of scope) could address it.
    func testCommonCameraMotionChangeCanStillManufactureACandidateKnownLimitation() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.1, y: 0), armTime: 0.5)
        var event: TouchCandidateEvent?
        for i in 1...5 {
            // A plausible gimbal-correction change, not a real touch —
            // indistinguishable from one using only raw ball velocity.
            event = tick(detector, t: 0.5 + Double(i) * 0.1, proximity: .near, ballVelocity: .init(x: 0.1, y: 0.2), distance: 1.0)
        }
        XCTAssertNotNil(event, "documents the known camera/gimbal-motion confound — this measurement is NOT camera-motion invariant")
    }

    func testBallPassesNearbyWithoutInteractionYieldsNoCandidate() {
        let detector = TouchCandidateDetector()
        approachThenArm(detector, velocity: .init(x: 0.2, y: 0), armTime: 0.5)
        // Passes straight through: near briefly, unchanged velocity, then away again.
        _ = tick(detector, t: 0.6, proximity: .near, ballVelocity: .init(x: 0.2, y: 0), distance: 1.0)
        let leaves = tick(detector, t: 0.7, proximity: .away, ballVelocity: .init(x: 0.2, y: 0), distance: 3.0)
        XCTAssertNil(leaves)
        XCTAssertEqual(detector.lastRejectionReason, .noMeaningfulMotionChange)
    }
}
