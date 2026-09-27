//
//  PlayerAnalysisEventDetectorTests.swift
//  KemetFCTrackerTests
//
//  Proves PlayerAnalysisEventDetector (Player Analysis V2.0 — see
//  PLAYER_ANALYSIS_V2_EVENT_DESIGN.md) only fires an event on a genuine,
//  debounced state transition/trend already present in a V1
//  PlayerLiveAnalysisSnapshot — never on jitter, never across a tracking
//  gap, never twice for the same hold/window, and never leaks state across
//  a reset. Tests feed hand-built snapshots directly (not through
//  PlayerAnalysisEngine) so the detector's own debounce/window/state-machine
//  logic is exercised in isolation from V1's separate hysteresis.
//

import XCTest
@testable import KemetFCTracker

final class PlayerAnalysisEventDetectorTests: XCTestCase {
    private func snapshot(
        t: TimeInterval,
        tracking: TrackingState = .locked,
        trackingConfidence: Double? = 0.9,
        movement: MovementState = .uncertain,
        ballVisible: Bool = false,
        ballConfidence: Double? = nil,
        proximity: BallProximityState = .unavailable,
        distance: Double? = nil
    ) -> PlayerLiveAnalysisSnapshot {
        PlayerLiveAnalysisSnapshot(
            timestamp: t, trackingState: tracking, trackingConfidence: trackingConfidence,
            movementState: movement, ballVisible: ballVisible, ballConfidence: ballConfidence,
            ballProximityState: proximity, normalizedPlayerBallDistance: distance
        )
    }

    private func types(_ events: [PlayerAnalysisEvent]) -> [PlayerAnalysisEventType] {
        events.map { $0.type }
    }

    // MARK: - Movement started/stopped (design doc section 3)

    func testStationaryToMovingFiresMovementStartedAfterHoldTime() {
        let detector = PlayerAnalysisEventDetector()
        // Establish a real stationary baseline FIRST — per the 2026-09-27
        // semantics refinement, `UNKNOWN → MOVING` alone (with no prior
        // established baseline) must never fire; only a transition AWAY FROM
        // an already-established baseline does.
        _ = detector.observe(snapshot: snapshot(t: -1.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: -0.5, movement: .stationary))
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 0.4, by: 0.1) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        XCTAssertTrue(fired.isEmpty, "must not fire before the minimum hold time")
        fired += detector.observe(snapshot: snapshot(t: 0.5, movement: .moving))
        XCTAssertEqual(types(fired), [.movementStarted])
        XCTAssertEqual(fired.first?.startTimestamp, 0.0, "start time is when the hold BEGAN, not when it was confirmed")
    }

    func testMovingToStationaryFiresMovementStopped() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .moving))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .moving)) // consumes movementStarted
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.1, movement: .stationary))
        XCTAssertEqual(types(fired), [.movementStopped])
    }

    func testThresholdJitterAlternatingNeverHoldsLongEnoughToFire() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        var moving = true
        for i in 0..<20 {
            let t = Double(i) * 0.1
            fired += detector.observe(snapshot: snapshot(t: t, movement: moving ? .moving : .stationary))
            moving.toggle()
        }
        XCTAssertTrue(fired.isEmpty, "rapid alternation (every 0.1s) never reaches the 0.5s hold minimum")
    }

    func testTrackingGapMidCandidateVoidsItRatherThanBridging() {
        let detector = PlayerAnalysisEventDetector()
        // Establish a real stationary baseline first so the later `.moving`
        // candidate is a genuine transition to test, not an initial-baseline
        // establishment (which never fires regardless of any gap).
        _ = detector.observe(snapshot: snapshot(t: -1.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: -0.5, movement: .stationary))
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.0, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 0.3, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 0.35, movement: .uncertain)) // tracking gap
        fired += detector.observe(snapshot: snapshot(t: 0.4, movement: .moving)) // reacquired, moving again
        fired += detector.observe(snapshot: snapshot(t: 0.7, movement: .moving)) // only 0.3s since 0.4 — not yet 0.5s
        XCTAssertTrue(fired.isEmpty, "the gap must restart the hold candidate, not bridge across it")
        fired += detector.observe(snapshot: snapshot(t: 0.9, movement: .moving))
        XCTAssertEqual(types(fired), [.movementStarted])
        XCTAssertEqual(fired.first?.startTimestamp, 0.4, "must measure the hold from the post-gap restart, not t=0.0")
    }

    func testStationaryHeldThroughoutNeverFiresMovementStarted() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 2.0, by: 0.2) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .stationary))
        }
        XCTAssertTrue(fired.isEmpty)
    }

    func testMovementResumesAfterStopFiresIndependentlyEachTime() {
        let detector = PlayerAnalysisEventDetector()
        // Establish a real stationary baseline first so the first `.moving`
        // below is a genuine, fireable transition rather than the initial
        // baseline establishment.
        _ = detector.observe(snapshot: snapshot(t: -1.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: -0.5, movement: .stationary))
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.0, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 0.5, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.1, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.2, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 1.7, movement: .moving))
        XCTAssertEqual(types(fired), [.movementStarted, .movementStopped, .movementStarted])
    }

    // MARK: - Movement event semantics refinement (2026-09-27): initial
    // baseline establishment is NOT an event. `movementStarted`/
    // `movementStopped` only fire on a genuine transition AWAY FROM an
    // already-established baseline, never on the first-ever confirmed state.
    // The 9 scenarios below are the exact regression set requested when this
    // was fixed (root cause: `lastMovementState` started at `.uncertain`, so
    // the very first confirmed state was treated as a "transition from
    // uncertain" and fired).

    /// 1. unknown → stationary = no event.
    func testUnknownToStationaryEstablishesBaselineWithNoEvent() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 2.0, by: 0.2) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .stationary))
        }
        XCTAssertTrue(fired.filter { $0.type == .movementStarted || $0.type == .movementStopped }.isEmpty)
    }

    /// 2. unknown → moving = no event.
    func testUnknownToMovingEstablishesBaselineWithNoEvent() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 2.0, by: 0.2) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        XCTAssertTrue(fired.filter { $0.type == .movementStarted || $0.type == .movementStopped }.isEmpty)
    }

    /// 3. stationary → moving = movementStarted once.
    func testEstablishedStationaryThenMovingFiresMovementStartedOnce() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .stationary)) // baseline established, silently
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 1.1, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 1.6, movement: .moving))
        XCTAssertEqual(fired.filter { $0.type == .movementStarted }.count, 1)
        XCTAssertTrue(fired.filter { $0.type == .movementStopped }.isEmpty)
    }

    /// 4. moving → stationary = movementStopped once.
    func testEstablishedMovingThenStationaryFiresMovementStoppedOnce() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .moving))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .moving)) // baseline established, silently
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.1, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.6, movement: .stationary))
        XCTAssertEqual(fired.filter { $0.type == .movementStopped }.count, 1)
        XCTAssertTrue(fired.filter { $0.type == .movementStarted }.isEmpty)
    }

    /// 5. stationary → moving → stationary = exactly two ordered events.
    func testStationaryMovingStationaryFiresExactlyTwoOrderedEvents() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .stationary)) // established
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 1.1, movement: .moving)) // → movementStarted
        fired += detector.observe(snapshot: snapshot(t: 1.2, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.7, movement: .stationary)) // → movementStopped
        XCTAssertEqual(types(fired), [.movementStarted, .movementStopped])
    }

    /// 6. threshold jitter around an established baseline = no duplicate transitions.
    func testJitterAroundAnEstablishedBaselineNeverFiresDuplicateTransitions() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .stationary)) // established stationary
        var fired: [PlayerAnalysisEvent] = []
        // Brief moving blips that never hold the full 0.5s.
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 0.7, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 0.8, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 0.9, movement: .stationary))
        XCTAssertTrue(fired.isEmpty, "jitter must never fire")
        // A real, held transition afterward must still fire exactly once.
        fired += detector.observe(snapshot: snapshot(t: 1.0, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 1.5, movement: .moving))
        XCTAssertEqual(types(fired), [.movementStarted])
    }

    /// 7. a brief unavailable/uncertain gap between two samples of the SAME
    /// established state must not manufacture a transition.
    func testTemporaryUnavailableBetweenSameStateNeverManufacturesATransition() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .stationary)) // established stationary
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.6, movement: .uncertain))
        fired += detector.observe(snapshot: snapshot(t: 0.7, movement: .stationary))
        fired += detector.observe(snapshot: snapshot(t: 1.2, movement: .stationary))
        XCTAssertTrue(fired.isEmpty, "reconfirming the SAME baseline across a brief gap must stay silent")
    }

    /// 8. reset, then stationary = no event leaked from the previous session.
    func testResetThenStationaryProducesNoEventFromPreviousSession() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .moving))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .moving)) // established moving, session 1

        detector.reset()

        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 100.0, through: 102.0, by: 0.2) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .stationary))
        }
        XCTAssertTrue(fired.isEmpty, "a fresh session must re-establish its own baseline, never inherit the previous player's")
    }

    /// 9. reset, then moving = no event leaked from the previous session.
    func testResetThenMovingProducesNoEventFromPreviousSession() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .stationary)) // established stationary, session 1

        detector.reset()

        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 100.0, through: 102.0, by: 0.2) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        XCTAssertTrue(fired.isEmpty)
    }

    // MARK: - Sustained movement (design doc section 3)

    func testSustainedMovementFiresOnceAfterMinDurationAndNotTwice() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 4.0, by: 0.5) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        XCTAssertEqual(fired.filter { $0.type == .sustainedMovement }.count, 1)
        let sustained = fired.first { $0.type == .sustainedMovement }
        XCTAssertEqual(sustained?.startTimestamp, 0.0)
        XCTAssertEqual(sustained?.supportingMeasurements.durationSeconds ?? 0, 3.0, accuracy: 0.001)
    }

    func testMovingStretchBrokenByStationaryResetsSustainedWindow() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 2.0, by: 0.5) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        fired += detector.observe(snapshot: snapshot(t: 2.1, movement: .stationary))
        for t in stride(from: 2.2, through: 4.0, by: 0.5) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        XCTAssertTrue(fired.filter { $0.type == .sustainedMovement }.isEmpty, "no single unbroken 3.0s stretch ever occurred")
    }

    func testMovingStretchBrokenByTrackingGapResetsSustainedWindow() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 2.0, by: 0.5) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        fired += detector.observe(snapshot: snapshot(t: 2.1, movement: .uncertain))
        for t in stride(from: 2.2, through: 4.0, by: 0.5) {
            fired += detector.observe(snapshot: snapshot(t: t, movement: .moving))
        }
        XCTAssertTrue(fired.filter { $0.type == .sustainedMovement }.isEmpty)
    }

    // MARK: - Near-zone state machine (design doc section 5)

    func testEnteringNearZoneFiresBallEnteredNearZone() {
        let detector = PlayerAnalysisEventDetector()
        let fired = detector.observe(snapshot: snapshot(t: 0.0, proximity: .near, distance: 1.0))
        XCTAssertEqual(types(fired), [.ballEnteredNearZone])
    }

    func testLeavingNearZoneFiresBallLeftNearZoneOnlyFromNear() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, proximity: .away, distance: 3.0))
        let noEventYet = detector.observe(snapshot: snapshot(t: 0.1, proximity: .away, distance: 3.0))
        XCTAssertTrue(noEventYet.isEmpty, "away→away is not a transition")
        _ = detector.observe(snapshot: snapshot(t: 0.2, proximity: .near, distance: 1.0))
        let left = detector.observe(snapshot: snapshot(t: 0.3, proximity: .away, distance: 3.0))
        XCTAssertEqual(types(left), [.ballLeftNearZone])
    }

    func testSustainedNearFiresOnceAfterMinDuration() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for t in stride(from: 0.0, through: 1.4, by: 0.2) {
            fired += detector.observe(snapshot: snapshot(t: t, proximity: .near, distance: 1.0))
        }
        XCTAssertEqual(fired.filter { $0.type == .ballStayedNear }.count, 1)
    }

    func testBriefBallDropoutDuringNearDoesNotResetSustainedTimer() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.0, proximity: .near, distance: 1.0))
        fired += detector.observe(snapshot: snapshot(t: 0.4, proximity: .unavailable))
        fired += detector.observe(snapshot: snapshot(t: 0.8, proximity: .near, distance: 1.0))
        fired += detector.observe(snapshot: snapshot(t: 1.1, proximity: .near, distance: 1.0))
        XCTAssertEqual(fired.filter { $0.type == .ballStayedNear }.count, 1, "a single dropout must not restart the 1.0s sustain timer")
    }

    func testUnavailableNeverFiresBallLeftNearZone() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, proximity: .near, distance: 1.0))
        let fired = detector.observe(snapshot: snapshot(t: 0.1, proximity: .unavailable))
        XCTAssertTrue(fired.isEmpty, "unavailable is a non-event interruption, never a departure")
    }

    func testBriefNearPassBelowSustainDurationOnlyFiresEnterAndLeave() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.0, proximity: .near, distance: 1.0))
        fired += detector.observe(snapshot: snapshot(t: 0.3, proximity: .away, distance: 3.0))
        XCTAssertEqual(types(fired), [.ballEnteredNearZone, .ballLeftNearZone])
    }

    // MARK: - Ball approach/separation (design doc section 4)

    func testCleanMonotonicApproachFiresBallApproaching() {
        let detector = PlayerAnalysisEventDetector()
        let distances = [2.0, 1.8, 1.6, 1.4, 1.2]
        var fired: [PlayerAnalysisEvent] = []
        for (i, d) in distances.enumerated() {
            fired += detector.observe(snapshot: snapshot(t: Double(i) * 0.1, distance: d))
        }
        XCTAssertEqual(types(fired), [.ballApproaching])
        XCTAssertEqual(fired.first?.supportingMeasurements.startValue, 2.0)
        XCTAssertEqual(fired.first?.supportingMeasurements.endValue, 1.2)
        XCTAssertEqual(fired.first?.supportingMeasurements.sampleCount, 5)
    }

    func testNoisyApproachWithinToleranceStillFires() {
        let detector = PlayerAnalysisEventDetector()
        let distances = [2.5, 2.2, 2.25, 1.9, 1.6] // one +0.05 bump, tolerance = 0.9*0.10 = 0.09
        var fired: [PlayerAnalysisEvent] = []
        for (i, d) in distances.enumerated() {
            fired += detector.observe(snapshot: snapshot(t: Double(i) * 0.1, distance: d))
        }
        XCTAssertEqual(types(fired), [.ballApproaching])
    }

    func testNoisyApproachExceedingToleranceDoesNotFire() {
        let detector = PlayerAnalysisEventDetector()
        let distances = [2.5, 2.2, 2.4, 1.9, 1.6] // one +0.2 bump, tolerance = 0.9*0.10 = 0.09
        var fired: [PlayerAnalysisEvent] = []
        for (i, d) in distances.enumerated() {
            fired += detector.observe(snapshot: snapshot(t: Double(i) * 0.1, distance: d))
        }
        XCTAssertTrue(fired.filter { $0.type == .ballApproaching || $0.type == .ballSeparating }.isEmpty)
    }

    func testConstantDistanceNeverFiresApproachOrSeparate() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        for i in 0..<5 {
            fired += detector.observe(snapshot: snapshot(t: Double(i) * 0.1, distance: 1.5))
        }
        XCTAssertTrue(fired.filter { $0.type == .ballApproaching || $0.type == .ballSeparating }.isEmpty)
    }

    func testIneligibleSampleMidWindowResetsWindowCompletely() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        // 3 eligible decreasing samples, not yet at the 5-sample threshold.
        fired += detector.observe(snapshot: snapshot(t: 0.0, distance: 2.0))
        fired += detector.observe(snapshot: snapshot(t: 0.1, distance: 1.9))
        fired += detector.observe(snapshot: snapshot(t: 0.2, distance: 1.8))
        // Dropout: ball/distance not measurable this tick — must discard the
        // partial window. (Tracking stays `.locked` so this test isolates the
        // approach-window reset from the separate tracking-availability edge
        // events, which are covered by their own tests above.)
        fired += detector.observe(snapshot: snapshot(t: 0.3, distance: nil))
        // Only 4 fresh eligible samples — must NOT evaluate yet if the window truly reset.
        fired += detector.observe(snapshot: snapshot(t: 0.4, distance: 1.7))
        fired += detector.observe(snapshot: snapshot(t: 0.5, distance: 1.6))
        fired += detector.observe(snapshot: snapshot(t: 0.6, distance: 1.5))
        fired += detector.observe(snapshot: snapshot(t: 0.7, distance: 1.4))
        XCTAssertTrue(fired.isEmpty, "must not evaluate on the 4th fresh sample if the dropout correctly reset the window")
        // 5th fresh sample completes a NEW window starting at 1.7, not the stale 2.0.
        fired += detector.observe(snapshot: snapshot(t: 0.8, distance: 1.3))
        XCTAssertEqual(types(fired), [.ballApproaching])
        XCTAssertEqual(fired.first?.supportingMeasurements.startValue, 1.7, "must start from the post-dropout window, not the discarded one")
    }

    func testSeparatingOnlyFiresWhenWindowStartedNear() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        // Never near — zoneState is .away for every sample in this window.
        let distances = [1.0, 1.3, 1.6, 1.9, 2.2]
        for (i, d) in distances.enumerated() {
            fired += detector.observe(snapshot: snapshot(t: Double(i) * 0.1, proximity: .away, distance: d))
        }
        XCTAssertTrue(fired.filter { $0.type == .ballSeparating }.isEmpty, "separation must never fire without a prior near relationship")
    }

    func testSeparatingFiresAfterHavingBeenNear() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        // First sample is `.near` — this becomes both the near-entry AND the
        // first sample of the approach window, so windowStartedNear = true.
        fired += detector.observe(snapshot: snapshot(t: 0.0, proximity: .near, distance: 1.0))
        fired += detector.observe(snapshot: snapshot(t: 0.1, proximity: .away, distance: 1.3))
        fired += detector.observe(snapshot: snapshot(t: 0.2, proximity: .away, distance: 1.6))
        fired += detector.observe(snapshot: snapshot(t: 0.3, proximity: .away, distance: 1.9))
        fired += detector.observe(snapshot: snapshot(t: 0.4, proximity: .away, distance: 2.2))
        XCTAssertTrue(fired.contains { $0.type == .ballSeparating })
    }

    // MARK: - Tracking/ball availability edges

    func testPlayerTemporarilyLostAndReacquiredFireCorrectly() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.0, tracking: .unavailable)) // baseline, no event
        fired += detector.observe(snapshot: snapshot(t: 0.1, tracking: .locked))
        fired += detector.observe(snapshot: snapshot(t: 0.2, tracking: .locked)) // no change
        fired += detector.observe(snapshot: snapshot(t: 0.3, tracking: .temporarilyLost))
        fired += detector.observe(snapshot: snapshot(t: 0.4, tracking: .locked))
        XCTAssertEqual(types(fired), [.playerReacquired, .playerTemporarilyLost, .playerReacquired])
    }

    func testBallBecameVisibleAndUnavailableFireCorrectly() {
        let detector = PlayerAnalysisEventDetector()
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 0.0, ballVisible: false)) // baseline
        fired += detector.observe(snapshot: snapshot(t: 0.1, ballVisible: true, ballConfidence: 0.3))
        fired += detector.observe(snapshot: snapshot(t: 0.2, ballVisible: true, ballConfidence: 0.3)) // no change
        fired += detector.observe(snapshot: snapshot(t: 0.3, ballVisible: false))
        XCTAssertEqual(types(fired), [.ballBecameVisible, .ballBecameUnavailable])
    }

    // MARK: - Reset / session isolation

    func testResetClearsEventTimelineAndInProgressCandidates() {
        let detector = PlayerAnalysisEventDetector()
        // A real, complete transition before reset (initial establishment
        // alone never produces an event — see the semantics-refinement tests
        // above — so this must establish a baseline, THEN transition).
        _ = detector.observe(snapshot: snapshot(t: 0.0, movement: .stationary))
        _ = detector.observe(snapshot: snapshot(t: 0.5, movement: .stationary)) // established stationary
        _ = detector.observe(snapshot: snapshot(t: 0.6, movement: .moving))
        _ = detector.observe(snapshot: snapshot(t: 1.1, movement: .moving)) // fires movementStarted
        XCTAssertFalse(detector.events.isEmpty)

        detector.reset()
        XCTAssertTrue(detector.events.isEmpty)

        // Reset must clear the ESTABLISHED baseline itself, not just the
        // event list — if session 1's `.moving` baseline leaked through,
        // this fresh `.stationary` establishment would be misread as a
        // moving→stationary transition and wrongly fire `movementStopped`.
        _ = detector.observe(snapshot: snapshot(t: 100.0, movement: .stationary))
        let establishFired = detector.observe(snapshot: snapshot(t: 100.5, movement: .stationary))
        XCTAssertTrue(establishFired.isEmpty, "must not leak the previous session's established state")

        // A fresh transition's hold must be timed from ITS OWN start.
        var fired: [PlayerAnalysisEvent] = []
        fired += detector.observe(snapshot: snapshot(t: 100.6, movement: .moving))
        fired += detector.observe(snapshot: snapshot(t: 100.9, movement: .moving)) // only 0.3s — not yet
        XCTAssertTrue(fired.isEmpty)
        fired += detector.observe(snapshot: snapshot(t: 101.1, movement: .moving))
        XCTAssertEqual(fired.first?.startTimestamp, 100.6)
    }

    // MARK: - Ordering / capacity

    func testEventsAreStrictlyOrderedByStartTimestamp() {
        let detector = PlayerAnalysisEventDetector()
        _ = detector.observe(snapshot: snapshot(t: 0.0, tracking: .locked, movement: .moving, proximity: .near, distance: 1.0))
        _ = detector.observe(snapshot: snapshot(t: 0.5, tracking: .locked, movement: .moving, proximity: .near, distance: 1.0))
        _ = detector.observe(snapshot: snapshot(t: 0.6, tracking: .temporarilyLost, movement: .uncertain, proximity: .away, distance: nil))
        _ = detector.observe(snapshot: snapshot(t: 1.0, tracking: .locked, movement: .moving, proximity: .away, distance: 3.0))
        let timestamps = detector.events.map { $0.startTimestamp }
        XCTAssertEqual(timestamps, timestamps.sorted(), "events must be appended in non-decreasing timestamp order")
    }

    func testEventCapacityBoundsMemoryKeepingMostRecent() {
        let detector = PlayerAnalysisEventDetector(eventCapacity: 3)
        var locked = false
        for i in 0..<10 {
            locked.toggle()
            _ = detector.observe(snapshot: snapshot(t: Double(i) * 0.1, tracking: locked ? .locked : .temporarilyLost))
        }
        XCTAssertEqual(detector.events.count, 3, "bounded event timeline must stay bounded")
        XCTAssertEqual(detector.events.last?.startTimestamp, 0.9, "must retain the most recent events, not the oldest")
    }
}
