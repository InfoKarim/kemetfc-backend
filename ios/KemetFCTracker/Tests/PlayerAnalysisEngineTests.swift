//
//  PlayerAnalysisEngineTests.swift
//  KemetFCTrackerTests
//
//  Proves PlayerAnalysisEngine derives only honest, documented states from
//  already-given tracker values — never invents tactical claims, never
//  reports movement/proximity from a stale/unobserved frame, and never
//  fabricates a summary fraction from zero samples.
//

import XCTest
@testable import KemetFCTracker

final class PlayerAnalysisEngineTests: XCTestCase {
    private func box(_ x: Double, _ y: Double = 0.5, w: Double = 0.2, h: Double = 0.4) -> NormalizedRect {
        NormalizedRect(x: x - w / 2, y: y - h / 2, width: w, height: h)
    }

    /// A tiny ball box whose CENTER is exactly `centerX`,`centerY` — avoids
    /// origin-vs-center arithmetic mistakes in the proximity tests below.
    private func ballAt(centerX: Double, centerY: Double = 0.5, size: Double = 0.02) -> NormalizedRect {
        NormalizedRect(x: centerX - size / 2, y: centerY - size / 2, width: size, height: size)
    }

    // MARK: - Tracking state

    func testLockedStateWithRealConfidenceIsReportedHonestly() {
        let engine = PlayerAnalysisEngine()
        let snapshot = engine.observe(
            playerState: .locked, playerConfidence: 0.87, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 1.0
        )
        XCTAssertEqual(snapshot.trackingState, .locked)
        XCTAssertEqual(snapshot.trackingConfidence, 0.87)
    }

    func testTemporaryLossReportsTemporarilyLostWithNoConfidence() {
        let engine = PlayerAnalysisEngine()
        let snapshot = engine.observe(
            playerState: .temporarilyLost, playerConfidence: 0, playerBox: nil,
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 1.0
        )
        XCTAssertEqual(snapshot.trackingState, .temporarilyLost)
        XCTAssertNil(snapshot.trackingConfidence)
    }

    func testFullLossReportsLostWithNoConfidence() {
        let engine = PlayerAnalysisEngine()
        let snapshot = engine.observe(
            playerState: .lost, playerConfidence: 0, playerBox: nil,
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 1.0
        )
        XCTAssertEqual(snapshot.trackingState, .lost)
        XCTAssertNil(snapshot.trackingConfidence)
    }

    func testNoPlayerEverLockedReportsUnavailableNotLost() {
        let engine = PlayerAnalysisEngine()
        let snapshot = engine.observe(
            playerState: nil, playerConfidence: 0, playerBox: nil,
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 1.0
        )
        XCTAssertEqual(snapshot.trackingState, .unavailable)
    }

    func testRecoveryReturnsToLockedState() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe(playerState: .locked, playerConfidence: 0.8, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 0)
        _ = engine.observe(playerState: .temporarilyLost, playerConfidence: 0, playerBox: nil, playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 1)
        let recovered = engine.observe(playerState: .reacquired, playerConfidence: 0.6, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 2)
        XCTAssertEqual(recovered.trackingState, .locked, "reacquired is exposed as locked — a real, confident commit")
    }

    // MARK: - Movement state

    func testStationarySequenceReportsStationaryAfterConfirmFrames() {
        let engine = PlayerAnalysisEngine()
        var last: PlayerLiveAnalysisSnapshot!
        for i in 0..<5 {
            last = engine.observe(
                playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
                playerVelocity: .init(x: 0.001, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i)
            )
        }
        XCTAssertEqual(last.movementState, .stationary)
    }

    func testMovingSequenceReportsMovingAfterConfirmFrames() {
        let engine = PlayerAnalysisEngine()
        var last: PlayerLiveAnalysisSnapshot!
        for i in 0..<5 {
            last = engine.observe(
                playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
                playerVelocity: .init(x: 0.2, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i)
            )
        }
        XCTAssertEqual(last.movementState, .moving)
    }

    func testNoisyStationarySequenceDoesNotFlickerToMoving() {
        // Alternating just-below/at-threshold speeds around a near-stationary
        // player should never accumulate enough consecutive frames to flip.
        let engine = PlayerAnalysisEngine()
        var sawMoving = false
        for i in 0..<10 {
            let vx = i % 2 == 0 ? 0.005 : 0.02
            let snap = engine.observe(
                playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
                playerVelocity: .init(x: vx, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i)
            )
            if snap.movementState == .moving { sawMoving = true }
        }
        XCTAssertFalse(sawMoving, "alternating below-threshold speeds must never confirm as moving")
    }

    func testThresholdCrossingRequiresConsecutiveFramesNotOne() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<5 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.001, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        // A single fast frame must not immediately flip the confirmed state.
        let firstFast = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.5, y: 0), ballBox: nil, ballConfidence: 0, at: 5)
        XCTAssertEqual(firstFast.movementState, .stationary, "one fast frame is not yet a confirmed transition")
    }

    func testHysteresisConfirmsAfterExactlyConfirmFrameCount() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<3 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        var last: PlayerLiveAnalysisSnapshot!
        for i in 0..<PlayerAnalysisV1Calibration.movementConfirmFrames {
            last = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.3, y: 0), ballBox: nil, ballConfidence: 0, at: Double(3 + i))
        }
        XCTAssertEqual(last.movementState, .moving, "exactly movementConfirmFrames consecutive frames must confirm the new state")
    }

    func testMissingTrackingReportsUncertainMovementImmediately() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<5 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.3, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let lost = engine.observe(playerState: .temporarilyLost, playerConfidence: 0, playerBox: nil, playerVelocity: .init(x: 0.3, y: 0), ballBox: nil, ballConfidence: 0, at: 5)
        XCTAssertEqual(lost.movementState, .uncertain, "losing observation must be reported immediately, never smoothed over with a stale velocity")
    }

    // MARK: - Ball proximity

    func testBallVisibleAndNearIsReportedNear() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: box(0.55), ballConfidence: 0.3, at: 0
        )
        XCTAssertTrue(snap.ballVisible)
        XCTAssertEqual(snap.ballProximityState, .near)
    }

    func testBallVisibleButFarIsReportedAway() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.2),
            playerVelocity: .init(x: 0, y: 0), ballBox: box(0.9), ballConfidence: 0.3, at: 0
        )
        XCTAssertTrue(snap.ballVisible)
        XCTAssertEqual(snap.ballProximityState, .away)
    }

    func testMissingBallIsUnavailableNotAway() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 0
        )
        XCTAssertFalse(snap.ballVisible)
        XCTAssertEqual(snap.ballProximityState, .unavailable)
    }

    func testLowConfidenceBallStillReportsRealConfidenceNeverInflated() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: box(0.55), ballConfidence: 0.21, at: 0
        )
        XCTAssertEqual(snap.ballConfidence, 0.21, "must pass through the detector's own low confidence unchanged")
    }

    func testProximityBoundaryUsesHysteresisNotAFlatSwitch() {
        let engine = PlayerAnalysisEngine()
        // playerBox width is 0.2 -> 1 body-width = 0.2 normalized units.
        // Establish "near" first (well inside the near threshold of 1.5 body-widths).
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52), ballConfidence: 0.3, at: 0)
        // Now sits at 1.75 body-widths — between the near (1.5) and away (2.0) bounds.
        // Must still report "near" (Schmitt trigger keeps the previous confirmed state).
        let midBall = ballAt(centerX: 0.5 + 1.75 * 0.2)
        let snap = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: midBall, ballConfidence: 0.3, at: 1)
        XCTAssertEqual(snap.ballProximityState, .near, "must not flip to away just past the near threshold — that's what the wider hysteresis band is for")
    }

    func testNoisyProximitySequenceAroundBoundaryDoesNotFlicker() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52), ballConfidence: 0.3, at: 0)
        var transitions = 0
        var previous: BallProximityState = .near
        for i in 1..<8 {
            // Both distances (1.6 and 1.9 body-widths) sit inside the hysteresis
            // band (near <=1.5 to become near, away >=2.0 to become away).
            let bodyWidths = (i % 2 == 0) ? 1.6 : 1.9
            let ball = ballAt(centerX: 0.5 + bodyWidths * 0.2)
            let snap = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ball, ballConfidence: 0.3, at: Double(i))
            if snap.ballProximityState != previous { transitions += 1 }
            previous = snap.ballProximityState
        }
        XCTAssertEqual(transitions, 0, "distances oscillating inside the hysteresis band must never toggle the reported state")
    }

    // MARK: - Summary

    func testSummaryReflectsMovementFraction() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<10 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.3, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.sampleCount, 10)
        XCTAssertGreaterThan(summary.movementActiveFraction, 0)
    }

    func testSummaryTrackingAvailabilityAndLostFraction() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<6 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        for i in 6..<10 {
            _ = engine.observe(playerState: .temporarilyLost, playerConfidence: 0, playerBox: nil, playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.trackingAvailableFraction, 0.6, accuracy: 0.0001)
        XCTAssertEqual(summary.trackingLostFraction, 0.4, accuracy: 0.0001)
    }

    func testSummaryBallVisibleAndNearFraction() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<4 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: box(0.55), ballConfidence: 0.3, at: Double(i))
        }
        for i in 4..<8 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.ballVisibleFraction, 0.5, accuracy: 0.0001)
        XCTAssertNotNil(summary.ballNearPlayerFraction)
    }

    func testEmptyAssessmentSummaryFabricatesNothing() {
        let engine = PlayerAnalysisEngine()
        let summary = engine.summarize()
        XCTAssertEqual(summary.sampleCount, 0)
        XCTAssertEqual(summary.trackingAvailableFraction, 0)
        XCTAssertNil(summary.ballNearPlayerFraction, "zero samples must never produce a fabricated rate")
    }

    func testPartialMissingObservationsStillProduceAnHonestSummary() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe(playerState: nil, playerConfidence: 0, playerBox: nil, playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 0)
        _ = engine.observe(playerState: .locked, playerConfidence: 0.7, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 1)
        let summary = engine.summarize()
        XCTAssertEqual(summary.sampleCount, 2)
        XCTAssertEqual(summary.trackingAvailableFraction, 0.5, accuracy: 0.0001)
    }

    func testResetClearsHistoryAndHysteresisState() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<5 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.3, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        engine.reset()
        XCTAssertEqual(engine.summarize().sampleCount, 0)
        // If the pre-reset "moving" streak (5 confirming frames) had survived, this
        // single post-reset frame would immediately confirm "moving" again. Instead
        // one frame is never enough on its own — proving the streak was cleared.
        let snap = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.3, y: 0), ballBox: nil, ballConfidence: 0, at: 0)
        XCTAssertEqual(snap.movementState, .uncertain, "a single frame right after reset must not yet confirm any state — hysteresis streak must not survive a reset")
    }

    func testHistoryCapacityIsBounded() {
        let engine = PlayerAnalysisEngine(historyCapacity: 5)
        for i in 0..<20 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        XCTAssertEqual(engine.history.count, 5)
        XCTAssertEqual(engine.history.last?.timestamp, 19, "must retain the most recent entries, not the oldest")
    }

    // MARK: - Pre-commit review: denominator correctness (section 1)

    func testBallNeverVisibleSummaryHasNilNearFractionNotZero() {
        let engine = PlayerAnalysisEngine()
        for i in 0..<10 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.ballVisibleFraction, 0)
        XCTAssertEqual(summary.proximityMeasurableSampleCount, 0)
        XCTAssertNil(summary.ballNearPlayerFraction, "never-measurable proximity must be nil, not a fabricated 0%")
    }

    func testBallVisibleOnlyBrieflyProducesCorrectBothFractions() {
        let engine = PlayerAnalysisEngine()
        // Ball visible+measurable for 2 of 10 samples.
        for i in 0..<2 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52), ballConfidence: 0.3, at: Double(i))
        }
        for i in 2..<10 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.ballVisibleFraction, 0.2, accuracy: 0.0001, "denominator is ALL samples")
        XCTAssertEqual(summary.proximityMeasurableSampleCount, 2)
        XCTAssertEqual(summary.ballNearPlayerFraction ?? -1, 1.0, accuracy: 0.0001, "denominator is MEASURABLE samples, not all samples")
    }

    func testBallVisibleButProximityUnavailableDueToLowConfidenceIsExcludedFromNearDenominator() {
        let engine = PlayerAnalysisEngine()
        // Ball visible every frame, but confidence is below the proximity floor.
        for i in 0..<5 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52), ballConfidence: 0.19, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.ballVisibleFraction, 1.0, "ballVisible reports the detector's own semantics, unaffected by the proximity floor")
        XCTAssertEqual(summary.proximityMeasurableSampleCount, 0)
        XCTAssertNil(summary.ballNearPlayerFraction, "ball was visible every frame, but never at a confidence proximity trusts — must be nil, not 0%")
    }

    func testMixedNearAwayUnavailableProducesCorrectFractions() {
        let engine = PlayerAnalysisEngine()
        // 2 near, 2 away, 2 unavailable (no ball) — 6 total samples.
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52), ballConfidence: 0.3, at: 0)
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.53), ballConfidence: 0.3, at: 1)
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.2), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.9), ballConfidence: 0.3, at: 2)
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.2), playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.91), ballConfidence: 0.3, at: 3)
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 4)
        _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: 5)
        let summary = engine.summarize()
        XCTAssertEqual(summary.sampleCount, 6)
        XCTAssertEqual(summary.proximityMeasurableSampleCount, 4, "only the 4 samples with a visible ball are measurable")
        XCTAssertEqual(summary.ballNearPlayerFraction ?? -1, 0.5, accuracy: 0.0001, "2 of 4 measurable samples were near")
    }

    func testTrackingUnavailableForPartOfSessionIsExcludedFromBothTrackingFractions() {
        let engine = PlayerAnalysisEngine()
        // No player ever locked for the first 3 samples (.unavailable), then locked for 7.
        for i in 0..<3 {
            _ = engine.observe(playerState: nil, playerConfidence: 0, playerBox: nil, playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        for i in 3..<10 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let summary = engine.summarize()
        XCTAssertEqual(summary.sampleCount, 10)
        XCTAssertEqual(summary.trackingAvailableFraction, 0.7, accuracy: 0.0001)
        XCTAssertEqual(summary.trackingLostFraction, 0, "unavailable is neither available nor lost")
        XCTAssertEqual(summary.trackingAvailableFraction + summary.trackingLostFraction, 0.7, accuracy: 0.0001, "the remaining 0.3 is the unavailable fraction, never hidden inside either metric")
    }

    // MARK: - Pre-commit review: full-session vs bounded history (section 2)

    func testSummaryRepresentsEntireSessionEvenBeyondHistoryCapacity() {
        let capacity = 50
        let totalSamples = 5000 // far longer than the bounded history buffer
        let engine = PlayerAnalysisEngine(historyCapacity: capacity)
        for i in 0..<totalSamples {
            // Alternate moving/stationary so we can check the summary isn't just
            // reflecting whatever happens to be the last `capacity` samples' pattern.
            let vx = (i % 2 == 0) ? 0.3 : 0.0
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: vx, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        XCTAssertEqual(engine.history.count, capacity, "bounded history must stay bounded")
        let summary = engine.summarize()
        XCTAssertEqual(summary.sampleCount, totalSamples, "the FULL-SESSION summary must count every sample ever observed, not just what the bounded history retained")
        XCTAssertEqual(summary.trackingAvailableFraction, 1.0, accuracy: 0.0001)
    }

    // MARK: - Pre-commit review: session isolation via reset (section 3)

    func testPlayerASessionDoesNotLeakIntoPlayerBAfterReset() {
        let engine = PlayerAnalysisEngine()
        // Player A: moving, ball near.
        for i in 0..<10 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5), playerVelocity: .init(x: 0.3, y: 0), ballBox: ballAt(centerX: 0.52), ballConfidence: 0.3, at: Double(i))
        }
        let playerASummary = engine.summarize()
        XCTAssertGreaterThan(playerASummary.movementActiveFraction, 0)
        XCTAssertNotNil(playerASummary.ballNearPlayerFraction)

        engine.reset() // the real trigger is TrackingCoordinator.lockPlayer(...) calling this

        // Player B: stationary, no ball, fewer samples.
        for i in 0..<4 {
            _ = engine.observe(playerState: .locked, playerConfidence: 0.8, playerBox: box(0.5), playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0, at: Double(i))
        }
        let playerBSummary = engine.summarize()
        XCTAssertEqual(playerBSummary.sampleCount, 4, "must contain ONLY Player B's 4 samples, none of Player A's 10")
        XCTAssertEqual(playerBSummary.movementActiveFraction, 0, "Player A's movement must not survive into Player B's summary")
        XCTAssertEqual(playerBSummary.ballVisibleFraction, 0)
        XCTAssertNil(playerBSummary.ballNearPlayerFraction, "Player A's ball-near data must not survive into Player B's summary")
    }

    // MARK: - Pre-commit review: ball-confidence eligibility for proximity (section 6)

    func testBallConfidenceExactlyAtProximityFloorIsEligible() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52),
            ballConfidence: PlayerAnalysisV1Calibration.minimumBallConfidenceForProximityAnalysis, at: 0
        )
        XCTAssertNotEqual(snap.ballProximityState, .unavailable, "the documented floor itself must be eligible, not excluded")
    }

    func testBallConfidenceJustBelowProximityFloorIsUnavailableButStillVisible() {
        let engine = PlayerAnalysisEngine()
        let belowFloor = PlayerAnalysisV1Calibration.minimumBallConfidenceForProximityAnalysis - 0.01
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.52),
            ballConfidence: belowFloor, at: 0
        )
        XCTAssertTrue(snap.ballVisible, "still reported visible — the detector's own semantics are never modified")
        XCTAssertEqual(snap.ballConfidence, belowFloor, "the real, unmodified confidence is still reported")
        XCTAssertEqual(snap.ballProximityState, .unavailable, "just below the floor must not produce a confident near/away claim")
    }

    // MARK: - Ball velocity (2026-09-27 wiring — raw vector, validity-gated)

    func testBallVelocityIsNilWhenBallNotVisible() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0,
            ballVelocity: .init(x: 0.3, y: 0.1), ballTrackId: 1, at: 0
        )
        XCTAssertFalse(snap.ballVisible)
        XCTAssertNil(snap.ballVelocity, "must never expose a velocity for a ball that isn't visible this frame")
    }

    func testBallVelocityExposesSuppliedVectorOnAContinuedObservationOfTheSameTrack() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe( // first confirmed observation of track 7 — velocity is nil regardless of this call
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.3), ballConfidence: 0.3,
            ballVelocity: .init(x: 0, y: 0), ballTrackId: 7, at: 0
        )
        let snap = engine.observe( // continued observation, same track
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.35), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.42, y: -0.1), ballTrackId: 7, at: 1.0
        )
        XCTAssertEqual(snap.ballVelocity, NormalizedPoint(x: 0.42, y: -0.1))
    }

    func testFirstConfirmedObservationOfANewTrackReturnsNilEvenWhenSuppliedVelocityIsZero() {
        let engine = PlayerAnalysisEngine()
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.3), ballConfidence: 0.3,
            ballVelocity: .init(x: 0, y: 0), ballTrackId: 1, at: 0
        )
        XCTAssertTrue(snap.ballVisible)
        XCTAssertNil(snap.ballVelocity, "BallTracker's own (0,0) on a track's first confirmed frame means 'not measured yet', never 'confirmed stationary'")
    }

    func testBriefMissFollowedBySameTrackIdDoesNotManufactureANewTrackCondition() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe( // first observation of track 5 — nil
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.3), ballConfidence: 0.3,
            ballVelocity: .init(x: 0, y: 0), ballTrackId: 5, at: 0
        )
        _ = engine.observe( // continued — valid
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.32), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.2, y: 0), ballTrackId: 5, at: 1.0
        )
        _ = engine.observe( // brief miss: BallTracker itself reports ballTrackId nil, ball not visible
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: nil, ballConfidence: 0,
            ballVelocity: .init(x: 0.2, y: 0), ballTrackId: nil, at: 2.0
        )
        let snap = engine.observe( // resume: the SAME track id (5) reappears
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.34), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.25, y: 0), ballTrackId: 5, at: 3.0
        )
        XCTAssertEqual(snap.ballVelocity, NormalizedPoint(x: 0.25, y: 0), "resuming the SAME track after a brief miss must not be treated as a new track")
    }

    func testGenuinelyDifferentTrackIdCausesBallVelocityToBeNilForItsFirstObservation() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe( // first observation of track 1 — nil
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.3), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.2, y: 0), ballTrackId: 1, at: 0
        )
        _ = engine.observe( // continued — valid
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.32), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.3, y: 0), ballTrackId: 1, at: 1.0
        )
        // A genuinely different track (id 2), with a deliberately NONZERO
        // supplied velocity — proving the rule is driven by the trackId
        // transition, not by "the raw value happens to be zero".
        let snap = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.7),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.7), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.99, y: 0.5), ballTrackId: 2, at: 2.0
        )
        XCTAssertNil(snap.ballVelocity, "a new trackId's first observation must be treated as unmeasured regardless of the supplied raw value")
    }

    func testResetClearsRememberedBallTrackIdentityAndFirstPostResetObservationIsNil() {
        let engine = PlayerAnalysisEngine()
        _ = engine.observe( // first observation of track 3 — nil
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.3), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.2, y: 0), ballTrackId: 3, at: 0
        )
        let established = engine.observe( // continued — valid
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.32), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.4, y: 0), ballTrackId: 3, at: 1.0
        )
        XCTAssertEqual(established.ballVelocity, NormalizedPoint(x: 0.4, y: 0))

        engine.reset()

        // SAME trackId (3) as before reset — BallTracker itself never
        // restarted this ball's track (a player re-lock does not touch
        // BallTracker) — but the engine's own remembered identity was
        // cleared, so this is conservatively treated as a first observation.
        let afterReset = engine.observe(
            playerState: .locked, playerConfidence: 0.9, playerBox: box(0.5),
            playerVelocity: .init(x: 0, y: 0), ballBox: ballAt(centerX: 0.3), ballConfidence: 0.3,
            ballVelocity: .init(x: 0.4, y: 0), ballTrackId: 3, at: 100.0
        )
        XCTAssertNil(afterReset.ballVelocity, "reset must not preserve hidden ball-track identity state across it")
    }
}
