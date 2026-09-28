//
//  TouchCandidateDetectorConfigurationTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 4 (baseline-equivalence — the highest-priority regression
//  test in the 2026-09-28 configuration-injection phase), Part 7
//  (TouchCandidateDetectorConfigurationSnapshot.validate()), and Part 9
//  (configuration isolation between detector instances).
//
//  Part 4's invariant is EXACT, not approximate: a detector constructed
//  with the implicit default (`TouchCandidateDetector()`, what
//  TrackingCoordinator actually uses in production) must behave IDENTICALLY
//  to one constructed with an explicit `.baseline` configuration, across
//  every scenario category the detector supports.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateDetectorConfigurationTests: XCTestCase {
    @discardableResult
    private func tick(
        _ detector: TouchCandidateDetector, t: TimeInterval,
        proximity: BallProximityState, ballVelocity: NormalizedPoint?,
        ballVisible: Bool = true, distance: Double? = 1.0,
        coMovementState: PlayerBallCoMovementState = .unavailable, ballTrackId: Int? = 1
    ) -> TouchCandidateEvent? {
        let analysis = PlayerLiveAnalysisSnapshot(
            timestamp: t, trackingState: .locked, trackingConfidence: 0.9,
            movementState: .uncertain, ballVisible: ballVisible, ballConfidence: ballVisible ? 0.3 : nil,
            ballProximityState: proximity, normalizedPlayerBallDistance: distance,
            ballVelocity: ballVelocity, ballTrackId: ballVisible ? ballTrackId : nil
        )
        let coMovement = PlayerBallCoMovementSnapshot(timestamp: t, state: coMovementState)
        return detector.observe(analysis: analysis, coMovement: coMovement)
    }

    private typealias TickPlan = [(t: TimeInterval, proximity: BallProximityState, velocity: NormalizedPoint?, visible: Bool, distance: Double?, coMovement: PlayerBallCoMovementState, trackId: Int?)]

    private func armTime(_ i: Int) -> TimeInterval { Double(i) * 0.1 }

    /// Runs the SAME tick plan through both a default-constructed detector
    /// (A) and an explicit-`.baseline` detector (B), asserting every
    /// persisted field — including `lastRejectionReason` — matches exactly,
    /// except each event's own `id` (a fresh UUID per instance).
    private func assertBaselineEquivalence(_ plan: TickPlan, file: StaticString = #filePath, line: UInt = #line) {
        let a = TouchCandidateDetector()
        let b = TouchCandidateDetector(configuration: .baseline)
        var aEvents: [TouchCandidateEvent] = []
        var bEvents: [TouchCandidateEvent] = []
        for step in plan {
            if let event = tick(a, t: step.t, proximity: step.proximity, ballVelocity: step.velocity, ballVisible: step.visible, distance: step.distance, coMovementState: step.coMovement, ballTrackId: step.trackId) {
                aEvents.append(event)
            }
            if let event = tick(b, t: step.t, proximity: step.proximity, ballVelocity: step.velocity, ballVisible: step.visible, distance: step.distance, coMovementState: step.coMovement, ballTrackId: step.trackId) {
                bEvents.append(event)
            }
            XCTAssertEqual(a.lastRejectionReason, b.lastRejectionReason, "rejection reason diverged at t=\(step.t)", file: file, line: line)
        }
        XCTAssertEqual(aEvents.count, bEvents.count, "event count diverged", file: file, line: line)
        for (eventA, eventB) in zip(aEvents, bEvents) {
            XCTAssertEqual(eventA.startTime, eventB.startTime, file: file, line: line)
            XCTAssertEqual(eventA.interactionTime, eventB.interactionTime, file: file, line: line)
            XCTAssertEqual(eventA.endTime, eventB.endTime, file: file, line: line)
            XCTAssertEqual(eventA.ballTrackId, eventB.ballTrackId, file: file, line: line)
            XCTAssertEqual(eventA.evidence, eventB.evidence, file: file, line: line)
        }
    }

    // MARK: - Part 4: baseline equivalence across scenario categories

    func testBaselineEquivalence_speedChangeCandidate() {
        var plan: TickPlan = []
        let approach = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, approach, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, approach, true, 1.0, .candidate, 1))
        let fast = NormalizedPoint(x: 0.5, y: 0)
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, fast, true, 1.0, .sustained, 1)) }
        assertBaselineEquivalence(plan)
    }

    func testBaselineEquivalence_directionChangeCandidate() {
        var plan: TickPlan = []
        let east = NormalizedPoint(x: 0.3, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, east, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, east, true, 1.0, .candidate, 1))
        let west = NormalizedPoint(x: -0.3, y: 0)
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, west, true, 1.0, .sustained, 1)) }
        assertBaselineEquivalence(plan)
    }

    func testBaselineEquivalence_rejectedNoMeaningfulChange() {
        var plan: TickPlan = []
        let v = NormalizedPoint(x: 0.15, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, v, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, v, true, 1.0, .unavailable, 1))
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, v, true, 1.0, .unavailable, 1)) }
        assertBaselineEquivalence(plan)
    }

    func testBaselineEquivalence_dropoutPath() {
        var plan: TickPlan = []
        let v = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, v, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, v, true, 1.0, .candidate, 1))
        plan.append((0.6, .unavailable, nil, false, nil, .unavailable, nil))
        let fast = NormalizedPoint(x: 0.6, y: 0.1)
        plan.append((0.7, .near, fast, true, 1.0, .sustained, 1))
        plan.append((0.8, .near, fast, true, 1.0, .sustained, 1))
        assertBaselineEquivalence(plan)
    }

    func testBaselineEquivalence_cooldownPath() {
        var plan: TickPlan = []
        let slow = NormalizedPoint(x: 0.1, y: 0)
        let fast = NormalizedPoint(x: 0.5, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, slow, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, slow, true, 1.0, .candidate, 1))
        plan.append((0.6, .near, fast, true, 1.0, .sustained, 1))
        plan.append((1.0, .near, fast, true, 1.0, .sustained, 1))
        for i in 0..<5 { plan.append((1.6 + Double(i) * 0.1, .away, fast, true, 3.0, .unavailable, 1)) }
        plan.append((2.1, .near, fast, true, 1.0, .candidate, 1))
        let slower = NormalizedPoint(x: -0.1, y: 0.3)
        plan.append((2.2, .near, slower, true, 1.0, .sustained, 1))
        plan.append((2.6, .near, slower, true, 1.0, .sustained, 1))
        assertBaselineEquivalence(plan)
    }

    func testBaselineEquivalence_ballTrackTransition() {
        var plan: TickPlan = []
        let v = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, v, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, v, true, 1.0, .candidate, 1))
        // Ball track changes mid-episode without a dropout.
        let fast = NormalizedPoint(x: 0.6, y: 0)
        plan.append((0.6, .near, fast, true, 1.0, .sustained, 2))
        plan.append((0.7, .near, fast, true, 1.0, .sustained, 2))
        assertBaselineEquivalence(plan)
    }

    // MARK: - Part 9: configuration isolation between instances

    func testConfigurationIsolationBetweenInterleavedInstances() {
        var lenient = TouchCandidateDetectorConfigurationSnapshot.baseline
        lenient.minimumSpeedChangeForTouchCandidate = 0.001
        let detectorA = TouchCandidateDetector(configuration: .baseline)
        let detectorB = TouchCandidateDetector(configuration: lenient)

        // A borderline speed change that baseline rejects but the lenient
        // configuration accepts — proves each instance genuinely reads its
        // OWN configuration, not a shared/global one, even when ticked
        // interleaved on the same thread with the same data.
        var plan: TickPlan = []
        let slow = NormalizedPoint(x: 0.10, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, slow, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, slow, true, 1.0, .candidate, 1))
        let borderline = NormalizedPoint(x: 0.115, y: 0) // Δspeed ≈ 0.015 < baseline 0.08, > lenient 0.001
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, borderline, true, 1.0, .sustained, 1)) }

        var eventsA: [TouchCandidateEvent] = []
        var eventsB: [TouchCandidateEvent] = []
        for step in plan {
            if let event = tick(detectorA, t: step.t, proximity: step.proximity, ballVelocity: step.velocity, distance: step.distance, coMovementState: step.coMovement, ballTrackId: step.trackId) {
                eventsA.append(event)
            }
            if let event = tick(detectorB, t: step.t, proximity: step.proximity, ballVelocity: step.velocity, distance: step.distance, coMovementState: step.coMovement, ballTrackId: step.trackId) {
                eventsB.append(event)
            }
        }

        XCTAssertTrue(eventsA.isEmpty, "baseline-configured detector must reject this borderline change")
        XCTAssertEqual(eventsB.count, 1, "lenient-configured detector must accept the SAME borderline change")
        XCTAssertEqual(detectorA.lastRejectionReason, .noMeaningfulMotionChange)

        // Re-verify baseline detector A was never mutated by B's presence:
        // feeding A a fresh identical borderline episode must still reject.
        detectorA.reset()
        var secondPlan: TickPlan = []
        for i in 0..<5 { secondPlan.append((10 + armTime(i), .away, slow, true, 3.0, .unavailable, 1)) }
        secondPlan.append((10.5, .near, slow, true, 1.0, .candidate, 1))
        for i in 1...5 { secondPlan.append((10.5 + Double(i) * 0.1, .near, borderline, true, 1.0, .sustained, 1)) }
        var eventsA2: [TouchCandidateEvent] = []
        for step in secondPlan {
            if let event = tick(detectorA, t: step.t, proximity: step.proximity, ballVelocity: step.velocity, distance: step.distance, coMovementState: step.coMovement, ballTrackId: step.trackId) {
                eventsA2.append(event)
            }
        }
        XCTAssertTrue(eventsA2.isEmpty, "detector A's configuration must remain baseline after B ran interleaved with it")
    }

    // MARK: - Part 7: TouchCandidateDetectorConfigurationSnapshot.validate()

    func testValidateAcceptsBaseline() {
        XCTAssertNoThrow(try TouchCandidateDetectorConfigurationSnapshot.baseline.validate())
    }

    func testValidateRejectsNegativePreWindow() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.preWindowSeconds = -0.1
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .negativeDuration(field: "preWindowSeconds"))
        }
    }

    func testValidateRejectsNegativePostWindow() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.postWindowSeconds = -1.0
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .negativeDuration(field: "postWindowSeconds"))
        }
    }

    func testValidateRejectsNegativeCooldown() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.cooldownDurationSeconds = -0.01
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .negativeDuration(field: "cooldownDurationSeconds"))
        }
    }

    func testValidateRejectsNegativeDropoutGrace() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.dropoutGraceDurationSeconds = -0.01
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .negativeDuration(field: "dropoutGraceDurationSeconds"))
        }
    }

    func testValidateRejectsNegativeSpeedChangeThreshold() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.minimumSpeedChangeForTouchCandidate = -0.01
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .negativeThreshold(field: "minimumSpeedChangeForTouchCandidate"))
        }
    }

    func testValidateRejectsNegativeMinimumSpeedForDirection() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.minimumSpeedForMeaningfulDirection = -0.01
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .negativeThreshold(field: "minimumSpeedForMeaningfulDirection"))
        }
    }

    func testValidateRejectsDirectionSimilarityAboveOne() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.maximumDirectionSimilarityForTouchCandidate = 1.5
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .directionSimilarityOutOfRange(value: 1.5))
        }
    }

    func testValidateRejectsDirectionSimilarityBelowNegativeOne() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.maximumDirectionSimilarityForTouchCandidate = -1.5
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .directionSimilarityOutOfRange(value: -1.5))
        }
    }

    func testValidateRejectsNaN() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.postWindowSeconds = .nan
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .nonFiniteValue(field: "postWindowSeconds"))
        }
    }

    func testValidateRejectsInfinity() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.cooldownDurationSeconds = .infinity
        XCTAssertThrowsError(try config.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateDetectorConfigurationSnapshotValidationError, .nonFiniteValue(field: "cooldownDurationSeconds"))
        }
    }

    func testValidateAcceptsZeroDurationsAndZeroThresholds() {
        // Degenerate but mathematically well-defined — zero is not invalid,
        // only negative/non-finite/out-of-range values are (Part 7: "do not
        // invent arbitrary upper limits").
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.preWindowSeconds = 0
        config.postWindowSeconds = 0
        config.cooldownDurationSeconds = 0
        config.dropoutGraceDurationSeconds = 0
        config.minimumSpeedChangeForTouchCandidate = 0
        config.minimumSpeedForMeaningfulDirection = 0
        XCTAssertNoThrow(try config.validate())
    }

    func testValidateAcceptsDirectionSimilarityAtExactBoundaries() {
        var config = TouchCandidateDetectorConfigurationSnapshot.baseline
        config.maximumDirectionSimilarityForTouchCandidate = 1.0
        XCTAssertNoThrow(try config.validate())
        config.maximumDirectionSimilarityForTouchCandidate = -1.0
        XCTAssertNoThrow(try config.validate())
    }
}
