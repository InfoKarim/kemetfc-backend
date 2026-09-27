//
//  PlayerBallCoMovementDetectorTests.swift
//  KemetFCTrackerTests
//
//  Proves PlayerBallCoMovementDetector (see PLAYER_ANALYSIS_V2_EVENT_DESIGN.md
//  and the 2026-09-27 co-movement audit) only reports `.sustained`/.candidate`
//  from genuine, sustained, direction+distance+stability evidence — never
//  from a single frame, never from unavailable data treated as negative
//  evidence, never across an artificial gap, and never leaking across a
//  reset. Also documents, rather than hides, the one confound this low-level
//  measurement cannot rule out: a shared camera translation (see the last
//  test in this file).
//

import XCTest
@testable import KemetFCTracker

final class PlayerBallCoMovementDetectorTests: XCTestCase {
    private func snapshot(
        t: TimeInterval,
        tracking: TrackingState = .locked,
        ballVisible: Bool = true,
        distance: Double? = nil,
        playerVelocity: NormalizedPoint? = nil,
        ballVelocity: NormalizedPoint? = nil,
        relativePosition: NormalizedPoint? = nil
    ) -> PlayerLiveAnalysisSnapshot {
        PlayerLiveAnalysisSnapshot(
            timestamp: t, trackingState: tracking, trackingConfidence: tracking == .locked ? 0.9 : nil,
            movementState: .uncertain, ballVisible: ballVisible, ballConfidence: ballVisible ? 0.3 : nil,
            ballProximityState: .unavailable, normalizedPlayerBallDistance: distance,
            ballVelocity: ballVelocity, playerVelocity: playerVelocity, relativeBallPosition: relativePosition
        )
    }

    /// A single "everything currently supports co-movement" tick's inputs —
    /// same direction, close, valid. Used to build up a sequence.
    private func supportingSnapshot(t: TimeInterval, relativePosition: NormalizedPoint = .init(x: 0.5, y: 0)) -> PlayerLiveAnalysisSnapshot {
        snapshot(
            t: t, distance: 1.0,
            playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: 0.2, y: 0),
            relativePosition: relativePosition
        )
    }

    /// Feeds a stable-relative-position supporting sequence from `t=0` to
    /// `t=until` in 0.1s steps and returns the LAST result. Builds each
    /// timestamp from a single multiplication (`Double(i) * 0.1`), not
    /// repeated addition, so no cumulative floating-point drift can creep
    /// into the duration comparisons the tests below depend on.
    @discardableResult
    private func feedSustainedSequence(_ detector: PlayerBallCoMovementDetector, until: TimeInterval) -> PlayerBallCoMovementSnapshot {
        var last = PlayerBallCoMovementSnapshot(timestamp: 0, state: .unavailable)
        let steps = Int((until / 0.1).rounded())
        for i in 0...steps {
            last = detector.observe(snapshot: supportingSnapshot(t: Double(i) * 0.1))
        }
        return last
    }

    // MARK: - Availability (unavailable vs notSupported — mandatory distinction)

    func testUnavailableWhenPlayerTrackingIsNotUsable() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(t: 0, tracking: .temporarilyLost))
        XCTAssertEqual(result.state, .unavailable)
    }

    func testUnavailableWhenBallIsNotVisible() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(t: 0, ballVisible: false))
        XCTAssertEqual(result.state, .unavailable)
    }

    func testUnavailableWhenBallVelocityIsInvalid() {
        // Mirrors a first-observation-of-a-new-ball-track tick, where V1
        // already reports ballVelocity == nil.
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(
            t: 0, distance: 1.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: nil, relativePosition: .init(x: 0.5, y: 0)
        ))
        XCTAssertEqual(result.state, .unavailable, "an invalid (first-observation) ball velocity must never be treated as a real zero measurement")
    }

    func testFirstBallTrackObservationStaysUnavailableThenBecomesUsableOnceValid() {
        let detector = PlayerBallCoMovementDetector()
        let firstTick = detector.observe(snapshot: snapshot( // V1 reports nil ballVelocity for the track's first frame
            t: 0, distance: 1.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: nil, relativePosition: .init(x: 0.5, y: 0)
        ))
        XCTAssertEqual(firstTick.state, .unavailable)
        let secondTick = detector.observe(snapshot: supportingSnapshot(t: 0.1))
        XCTAssertNotEqual(secondTick.state, .sustained, "a single subsequent tick can never immediately be sustained")
    }

    func testUnavailableAndNotSupportedAreDistinctForDifferentRealScenarios() {
        let unavailableDetector = PlayerBallCoMovementDetector()
        let unavailable = unavailableDetector.observe(snapshot: snapshot(t: 0, ballVisible: false))

        let notSupportedDetector = PlayerBallCoMovementDetector()
        let notSupported = notSupportedDetector.observe(snapshot: snapshot(
            t: 0, distance: 1.0, playerVelocity: .init(x: 0, y: 0), ballVelocity: .init(x: 0.2, y: 0), relativePosition: .init(x: 0.5, y: 0)
        ))

        XCTAssertEqual(unavailable.state, .unavailable)
        XCTAssertEqual(notSupported.state, .notSupported)
        XCTAssertNotEqual(unavailable.state, notSupported.state, "NO DATA and VALID DATA THAT DOES NOT SUPPORT must never be conflated")
    }

    // MARK: - Real counter-evidence (Part 9, scenarios 1-5)

    func testZeroSpeedPlayerDoesNotSupportCoMovement() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(
            t: 0, distance: 1.0, playerVelocity: .init(x: 0, y: 0), ballVelocity: .init(x: 0.2, y: 0), relativePosition: .init(x: 0.5, y: 0)
        ))
        XCTAssertEqual(result.state, .notSupported, "both velocities being non-nil but the player being stationary must not report co-movement")
    }

    func testZeroSpeedBallDoesNotSupportCoMovement() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(
            t: 0, distance: 1.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: 0, y: 0), relativePosition: .init(x: 0.5, y: 0)
        ))
        XCTAssertEqual(result.state, .notSupported)
    }

    func testBothStationaryDoesNotSupportCoMovement() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(
            t: 0, distance: 1.0, playerVelocity: .init(x: 0, y: 0), ballVelocity: .init(x: 0, y: 0), relativePosition: .init(x: 0.5, y: 0)
        ))
        XCTAssertEqual(result.state, .notSupported, "two zero vectors must never be reported as 'moving together'")
    }

    func testOppositeDirectionMovementDoesNotSupportCoMovement() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(
            t: 0, distance: 1.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: -0.2, y: 0), relativePosition: .init(x: 0.5, y: 0)
        ))
        XCTAssertEqual(result.evidence.directionalSimilarity ?? 1, -1, accuracy: 1e-9)
        XCTAssertEqual(result.state, .notSupported)
    }

    func testSimilarDirectionButFarApartDoesNotSupportMeaningfulCoMovement() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: snapshot(
            t: 0, distance: 5.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: 0.2, y: 0), relativePosition: .init(x: 5.0, y: 0)
        ))
        XCTAssertEqual(result.state, .notSupported, "identical direction far apart is not a meaningful player-ball relationship")
    }

    // MARK: - Temporal persistence (Part 6/9 scenario 6)

    func testOneFrameDirectionalCoincidenceNeverCreatesSustainedCoMovement() {
        let detector = PlayerBallCoMovementDetector()
        let result = detector.observe(snapshot: supportingSnapshot(t: 0))
        XCTAssertNotEqual(result.state, .sustained)
        XCTAssertNotEqual(result.state, .candidate, "a single tick has no drift history yet and must not even reach candidate")
    }

    func testSustainedValidRelationshipEventuallyReportsSustained() {
        let detector = PlayerBallCoMovementDetector()
        let final = feedSustainedSequence(detector, until: 1.6) // comfortable margin past the 1.0s sustain minimum
        XCTAssertEqual(final.state, .sustained)
        XCTAssertNotNil(final.evidence.supportingDurationSeconds)
        XCTAssertGreaterThanOrEqual(final.evidence.supportingDurationSeconds ?? 0, PlayerBallCoMovementCalibration.minimumSupportingDurationSeconds)
    }

    func testCandidateReportedBeforeSustainDurationIsReached() {
        let detector = PlayerBallCoMovementDetector()
        // Enough for the drift window (>=0.5s) but not yet the full 1.0s sustain duration.
        let result = feedSustainedSequence(detector, until: 0.7)
        XCTAssertEqual(result.state, .candidate)
    }

    // MARK: - Dropouts / resumption (Part 9 scenarios 7-8, Part 5)

    func testTemporaryBallDropoutDuringASustainedRelationshipIsReportedUnavailableThenResumes() {
        let detector = PlayerBallCoMovementDetector()
        _ = feedSustainedSequence(detector, until: 1.5) // now sustained

        let duringGap = detector.observe(snapshot: snapshot(t: 1.6, ballVisible: false))
        XCTAssertEqual(duringGap.state, .unavailable, "a miss must be reported as unavailable, never as sustained using stale velocity")

        let resumed = detector.observe(snapshot: supportingSnapshot(t: 1.7)) // well within the 0.5s grace
        XCTAssertEqual(resumed.state, .sustained, "a brief, grace-tolerated dropout must not force the streak back to candidate")
    }

    func testResumeAfterDropoutContinuesTheOriginalStreakDurationNotAFreshOne() {
        let detector = PlayerBallCoMovementDetector()
        _ = feedSustainedSequence(detector, until: 1.5)
        _ = detector.observe(snapshot: snapshot(t: 1.6, ballVisible: false))
        let resumed = detector.observe(snapshot: supportingSnapshot(t: 1.7))
        // Duration measured from the ORIGINAL streak start (t≈0.5), not from
        // the resume tick — i.e., meaningfully larger than the gap alone.
        XCTAssertGreaterThan(resumed.evidence.supportingDurationSeconds ?? 0, 1.0)
    }

    func testGenuinelyNewConditionAfterAGapExceedingGraceDoesNotResumeAsSustained() {
        let detector = PlayerBallCoMovementDetector()
        _ = feedSustainedSequence(detector, until: 1.5) // sustained

        // A gap well beyond the 0.5s grace duration.
        _ = detector.observe(snapshot: snapshot(t: 3.0, ballVisible: false))

        let resumed = detector.observe(snapshot: supportingSnapshot(t: 3.1))
        XCTAssertNotEqual(resumed.state, .sustained, "a gap beyond the grace duration must not let an old streak silently resume")
    }

    func testObservationGapAloneNeverManufacturesCoMovement() {
        // Two isolated, otherwise-eligible ticks separated by a large jump,
        // with NO prior streak at all — the second tick must not claim
        // stability from a single fresh sample.
        let detector = PlayerBallCoMovementDetector()
        _ = detector.observe(snapshot: supportingSnapshot(t: 0))
        let afterGap = detector.observe(snapshot: supportingSnapshot(t: 10.0))
        XCTAssertNotEqual(afterGap.state, .sustained)
        XCTAssertNotEqual(afterGap.state, .candidate, "insufficient drift history after a large gap must not be treated as stable")
    }

    // MARK: - Reset / session isolation (Part 9 scenario 9)

    func testResetClearsTemporalCoMovementEvidence() {
        let detector = PlayerBallCoMovementDetector()
        _ = feedSustainedSequence(detector, until: 1.5)

        detector.reset()

        let afterReset = detector.observe(snapshot: supportingSnapshot(t: 100.0))
        XCTAssertNotEqual(afterReset.state, .sustained)
        XCTAssertNotEqual(afterReset.state, .candidate, "reset must not leave any drift/streak memory from the previous session")
    }

    // MARK: - Timestamps (Part 9 scenario — irregular timestamps)

    func testNonAdvancingTimestampIsUnavailableAndDoesNotCorruptAnInProgressStreak() {
        let detector = PlayerBallCoMovementDetector()
        _ = feedSustainedSequence(detector, until: 1.5) // sustained, lastSupportingTimestamp == 1.5

        let duplicate = detector.observe(snapshot: supportingSnapshot(t: 1.5)) // same timestamp again
        XCTAssertEqual(duplicate.state, .unavailable)

        let backward = detector.observe(snapshot: supportingSnapshot(t: 1.4)) // earlier than last seen
        XCTAssertEqual(backward.state, .unavailable)

        // The streak must be unaffected by either bad tick.
        let resumed = detector.observe(snapshot: supportingSnapshot(t: 1.6))
        XCTAssertEqual(resumed.state, .sustained)
    }

    // MARK: - Relative-position stability / drift (Part 5, Part 9 scenarios 11-12)

    func testRelativePositionStabilityIsReflectedInEvidence() {
        let detector = PlayerBallCoMovementDetector()
        let final = feedSustainedSequence(detector, until: 1.5)
        XCTAssertEqual(final.evidence.relativePositionDrift ?? -1, 0, accuracy: 1e-9)
    }

    func testExcessiveRelativePositionDriftPreventsCoMovementEvenWithGoodDirectionAndDistance() {
        let detector = PlayerBallCoMovementDetector()
        var lastResult = PlayerBallCoMovementSnapshot(timestamp: 0, state: .unavailable)
        // The relative position drifts steadily — same direction/speed/
        // distance gate values, but the RELATIONSHIP itself is not stable.
        var t = 0.0
        var driftingX = 0.5
        while t <= 1.5 {
            lastResult = detector.observe(snapshot: snapshot(
                t: t, distance: 1.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: 0.2, y: 0),
                relativePosition: .init(x: driftingX, y: 0)
            ))
            driftingX += 0.2 // well beyond the 0.5 body-width drift ceiling within the window
            t += 0.1
        }
        XCTAssertNotEqual(lastResult.state, .sustained)
        XCTAssertNotEqual(lastResult.state, .candidate, "excessive relative-position drift must veto co-movement even when direction/speed/distance all pass")
    }

    func testBriefNearBallCrossingWithoutSustainedRelationshipNeverBecomesSustained() {
        // The ball passes near the player with brief directional similarity,
        // then the relationship breaks (distance grows) before the minimum
        // supporting duration is reached.
        let detector = PlayerBallCoMovementDetector()
        var lastResult = PlayerBallCoMovementSnapshot(timestamp: 0, state: .unavailable)
        for i in 0..<6 { // 0.6s of good-looking data — short of the 1.0s minimum
            lastResult = detector.observe(snapshot: supportingSnapshot(t: Double(i) * 0.1))
        }
        // Ball now separates rapidly — real counter-evidence.
        lastResult = detector.observe(snapshot: snapshot(
            t: 0.7, distance: 5.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: 0.2, y: 0), relativePosition: .init(x: 5.0, y: 0)
        ))
        XCTAssertEqual(lastResult.state, .notSupported)

        // Continuing to look "good" again afterward must restart from
        // scratch, never resume the earlier partial streak.
        let resumed = detector.observe(snapshot: supportingSnapshot(t: 0.8))
        XCTAssertNotEqual(resumed.state, .sustained)
    }

    func testPlayerRunningNearAMovingBallWithoutStableRelativeRelationshipNeverBecomesSustained() {
        // Same direction, close distance, but the relative position keeps
        // changing meaningfully tick to tick (no stable relationship).
        let detector = PlayerBallCoMovementDetector()
        var lastResult = PlayerBallCoMovementSnapshot(timestamp: 0, state: .unavailable)
        let driftValues: [Double] = [0.3, 0.9, 0.2, 1.1, 0.4, 1.0, 0.3, 0.95, 0.25, 1.05, 0.35, 0.9, 0.3, 1.0, 0.4, 0.9]
        for (i, x) in driftValues.enumerated() {
            lastResult = detector.observe(snapshot: snapshot(
                t: Double(i) * 0.1, distance: 1.0, playerVelocity: .init(x: 0.2, y: 0), ballVelocity: .init(x: 0.2, y: 0),
                relativePosition: .init(x: x, y: 0)
            ))
        }
        XCTAssertNotEqual(lastResult.state, .sustained)
    }

    // MARK: - Known, documented, unfixed limitation (Part 9 scenario 10)

    /// Simulates a common camera translation: BOTH the player and ball
    /// receive an IDENTICAL image-space velocity, and their relative
    /// position stays perfectly stable — exactly what a pure gimbal pan
    /// would produce even if both were real-world stationary. This test
    /// documents, per Part 9's explicit instruction, that the current
    /// design CANNOT rule this out: it deliberately asserts the (undesired,
    /// but honestly limited) `.sustained` outcome, so the limitation is a
    /// visible regression test, not a silently-tuned-away assumption. Fixing
    /// this would require real gimbal-orientation telemetry (not available
    /// in this codebase) to subtract the shared camera motion — out of scope
    /// for this phase. Do NOT "fix" this test by tuning thresholds; fix the
    /// underlying camera-motion-compensation gap instead, in a future phase.
    func testCommonCameraTranslationCanStillProduceASustainedResultKnownLimitation() {
        let detector = PlayerBallCoMovementDetector()
        var lastResult = PlayerBallCoMovementSnapshot(timestamp: 0, state: .unavailable)
        // Built from a single multiplication per step (not repeated `+=`), so
        // no cumulative floating-point drift can shave the final duration
        // just under the 1.0s sustain threshold.
        for i in 0...16 { // through t = 1.6, a comfortable margin past 1.0s of support
            let t = Double(i) * 0.1
            // Identical vectors for both objects (a shared pan), and a
            // perfectly stable relative position (both shift together).
            lastResult = detector.observe(snapshot: snapshot(
                t: t, distance: 1.0, playerVelocity: .init(x: 0.15, y: 0.05), ballVelocity: .init(x: 0.15, y: 0.05),
                relativePosition: .init(x: 0.5, y: 0)
            ))
        }
        XCTAssertEqual(lastResult.state, .sustained, "documents the known common-camera-motion confound — this measurement is NOT camera-motion invariant")
    }
}
