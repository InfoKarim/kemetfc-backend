//
//  TouchCandidateReplayEngineTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 18 (REPLAY, items 29-44) and Part 19 (performance sanity).
//  The single most important test in this file is
//  `testLiveAndReplayProduceEquivalentEventsForMeaningfulSpeedChange` (and
//  its siblings): it feeds the SAME synthetic tick sequence to (a) a live
//  `TouchCandidateDetector` directly and (b) the SAME sequence captured as
//  `TouchCandidateObservationRecord`s and run through
//  `TouchCandidateReplayEngine`, then asserts the two event lists agree on
//  everything EXCEPT `id` (a fresh UUID is expected to differ between the
//  two independent detector instances — see `TouchCandidateEvent.id`'s own
//  doc). This is the ongoing regression guard the file-level doc in
//  `TouchCandidateObservationRecord.reconstructedSnapshots()` refers to.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateReplayEngineTests: XCTestCase {
    // MARK: - Shared tick helpers (mirrors TouchCandidateDetectorTests exactly)

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

    /// Builds a raw tick plan as (timestamp, proximity, velocity, ballVisible,
    /// distance, coMovement, ballTrackId) tuples — one call site feeds BOTH a
    /// live detector and an observation-capture builder from the identical
    /// data, so there is no possibility of the two diverging by construction.
    private typealias TickPlan = [(t: TimeInterval, proximity: BallProximityState, velocity: NormalizedPoint?, visible: Bool, distance: Double?, coMovement: PlayerBallCoMovementState, trackId: Int?)]

    private func meaningfulSpeedChangePlan() -> TickPlan {
        var plan: TickPlan = []
        let approach = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 {
            plan.append((armTime(i), .away, approach, true, 3.0, .unavailable, 1))
        }
        plan.append((0.5, .near, approach, true, 1.0, .candidate, 1))
        let fast = NormalizedPoint(x: 0.5, y: 0)
        for i in 1...5 {
            plan.append((0.5 + Double(i) * 0.1, .near, fast, true, 1.0, .sustained, 1))
        }
        return plan
    }

    private func armTime(_ i: Int) -> TimeInterval { 0.5 - 0.5 + Double(i) * 0.1 }

    private func noEventPlan() -> TickPlan {
        var plan: TickPlan = []
        let v = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 {
            plan.append((armTime(i), .away, v, true, 3.0, .unavailable, 1))
        }
        plan.append((0.5, .near, v, true, 1.0, .unavailable, 1))
        for i in 1...5 {
            plan.append((0.5 + Double(i) * 0.1, .near, v, true, 1.0, .unavailable, 1))
        }
        return plan
    }

    private func runLive(_ plan: TickPlan) -> [TouchCandidateEvent] {
        let detector = TouchCandidateDetector()
        var events: [TouchCandidateEvent] = []
        for step in plan {
            if let event = tick(detector, t: step.t, proximity: step.proximity, ballVelocity: step.velocity, ballVisible: step.visible, distance: step.distance, coMovementState: step.coMovement, ballTrackId: step.trackId) {
                events.append(event)
            }
        }
        return events
    }

    private func buildCapture(_ plan: TickPlan, assessmentId: String = "A1") throws -> TouchCandidateObservationCapture {
        let observations = plan.map { step in
            TouchCandidateObservationRecord(
                timestampSeconds: step.t, ballVisible: step.visible, ballVelocity: step.velocity,
                normalizedPlayerBallDistance: step.distance, ballProximityState: step.proximity,
                ballTrackId: step.visible ? step.trackId : nil, coMovementState: step.coMovement
            )
        }
        return try TouchCandidateObservationCapture(assessmentId: assessmentId, observations: observations)
    }

    /// Compares two exported predictions on every field EXCEPT `eventId`
    /// (expected to differ — see file header).
    private func assertPredictionsEquivalent(_ a: [ExportedTouchCandidatePrediction], _ b: [ExportedTouchCandidatePrediction], file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(a.count, b.count, "event counts must match", file: file, line: line)
        for (lhs, rhs) in zip(a, b) {
            XCTAssertEqual(lhs.interactionTimeSeconds, rhs.interactionTimeSeconds, file: file, line: line)
            XCTAssertEqual(lhs.ballTrackId, rhs.ballTrackId, file: file, line: line)
            XCTAssertEqual(lhs.speedChange, rhs.speedChange, file: file, line: line)
            XCTAssertEqual(lhs.directionSimilarity, rhs.directionSimilarity, file: file, line: line)
            XCTAssertEqual(lhs.minimumPlayerBallDistance, rhs.minimumPlayerBallDistance, file: file, line: line)
        }
    }

    // MARK: - Baseline live-vs-replay equivalence (29-32, 39 — the core invariant)

    func testLiveAndReplayProduceEquivalentEventsForMeaningfulSpeedChange() throws {
        let plan = meaningfulSpeedChangePlan()
        let liveEvents = runLive(plan)
        XCTAssertEqual(liveEvents.count, 1, "sanity: this plan must actually produce a candidate")

        let capture = try buildCapture(plan)
        let result = try TouchCandidateReplayEngine.replay(capture: capture)

        assertPredictionsEquivalent(liveEvents.map { ExportedTouchCandidatePrediction(from: $0) }, result.events)
    }

    func testLiveAndReplayAgreeOnNoEventForUnchangedTrajectory() throws {
        let plan = noEventPlan()
        let liveEvents = runLive(plan)
        XCTAssertTrue(liveEvents.isEmpty)

        let capture = try buildCapture(plan)
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertTrue(result.events.isEmpty)
    }

    func testLiveAndReplayAgreeAcrossADropoutAndBallTrackChange() throws {
        var plan: TickPlan = []
        let v = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 {
            plan.append((armTime(i), .away, v, true, 3.0, .unavailable, 1))
        }
        plan.append((0.5, .near, v, true, 1.0, .candidate, 1))
        // Brief dropout during post-window collection.
        plan.append((0.6, .unavailable, nil, false, nil, .unavailable, nil))
        // Ball reappears on a DIFFERENT track with a meaningfully changed velocity.
        let fast = NormalizedPoint(x: 0.6, y: 0.1)
        plan.append((0.7, .near, fast, true, 1.0, .sustained, 2))
        plan.append((0.8, .near, fast, true, 1.0, .sustained, 2))

        let liveEvents = runLive(plan)
        let capture = try buildCapture(plan)
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        assertPredictionsEquivalent(liveEvents.map { ExportedTouchCandidatePrediction(from: $0) }, result.events)
    }

    func testLiveAndReplayAgreeThroughCooldownAndASecondEpisode() throws {
        var plan: TickPlan = []
        let slow = NormalizedPoint(x: 0.1, y: 0)
        let fast = NormalizedPoint(x: 0.5, y: 0)
        for i in 0..<5 {
            plan.append((armTime(i), .away, slow, true, 3.0, .unavailable, 1))
        }
        plan.append((0.5, .near, slow, true, 1.0, .candidate, 1))
        plan.append((0.6, .near, fast, true, 1.0, .sustained, 1))
        plan.append((1.0, .near, fast, true, 1.0, .sustained, 1)) // episode concludes (postWindow elapsed)
        // cooldown, then leave and re-approach for a second, distinct episode
        for i in 0..<5 {
            plan.append((1.6 + Double(i) * 0.1, .away, fast, true, 3.0, .unavailable, 1))
        }
        plan.append((2.1, .near, fast, true, 1.0, .candidate, 1))
        let slower = NormalizedPoint(x: -0.1, y: 0.3)
        plan.append((2.2, .near, slower, true, 1.0, .sustained, 1))
        plan.append((2.6, .near, slower, true, 1.0, .sustained, 1))

        let liveEvents = runLive(plan)
        XCTAssertGreaterThanOrEqual(liveEvents.count, 1, "sanity: at least one episode should resolve in this plan")
        let capture = try buildCapture(plan)
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        assertPredictionsEquivalent(liveEvents.map { ExportedTouchCandidatePrediction(from: $0) }, result.events)
    }

    func testReplayIsDeterministicAcrossRepeatedRuns() throws {
        let capture = try buildCapture(meaningfulSpeedChangePlan())
        let result1 = try TouchCandidateReplayEngine.replay(capture: capture)
        let result2 = try TouchCandidateReplayEngine.replay(capture: capture)
        assertPredictionsEquivalent(result1.events, result2.events)
        XCTAssertEqual(result1.events.count, result2.events.count)
    }

    func testReplayNeverMutatesTheOriginalCaptureObservations() throws {
        let capture = try buildCapture(meaningfulSpeedChangePlan())
        let before = capture.observations
        _ = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertEqual(capture.observations, before, "replay must never mutate the source capture — TouchCandidateObservationCapture is a value type read via `let` throughout the engine")
    }

    // MARK: - Single-observation / minimal sequences (33-34)

    func testSingleObservationReplayNeverCrashesAndNeverEmits() throws {
        let capture = try buildCapture([(0.0, .near, .init(x: 0.1, y: 0), true, 1.0, .unavailable, 1)])
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertTrue(result.events.isEmpty)
    }

    func testEmptyObservationsReplayYieldsEmptyResult() throws {
        let capture = try TouchCandidateObservationCapture(assessmentId: "A1", observations: [])
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertTrue(result.events.isEmpty)
        XCTAssertEqual(result.sourceObservationCount, 0)
    }

    // MARK: - Schema/configuration gating (35-38)

    func testIncompatibleSchemaVersionIsRejected() throws {
        var capture = try buildCapture(noEventPlan())
        capture.schemaVersion = TouchCandidateObservationCapture.currentSchemaVersion + 1
        XCTAssertThrowsError(try TouchCandidateReplayEngine.replay(capture: capture)) { error in
            XCTAssertEqual(error as? TouchCandidateReplayError, .incompatibleSchemaVersion(found: TouchCandidateObservationCapture.currentSchemaVersion + 1, expected: TouchCandidateObservationCapture.currentSchemaVersion))
        }
    }

    func testNonBaselineConfigurationIsRejectedNotSilentlySubstituted() throws {
        let capture = try buildCapture(noEventPlan())
        var nonBaseline = TouchCandidateDetectorConfigurationSnapshot.baseline
        nonBaseline.minimumSpeedChangeForTouchCandidate += 1.0
        XCTAssertThrowsError(try TouchCandidateReplayEngine.replay(capture: capture, configuration: nonBaseline)) { error in
            XCTAssertEqual(error as? TouchCandidateReplayError, .unsupportedConfiguration)
        }
    }

    func testBaselineConfigurationFingerprintChangeIsDetectable() throws {
        let capture = try buildCapture(noEventPlan())
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertEqual(result.configurationFingerprint, TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)
    }

    func testResultCarriesSourceProvenance() throws {
        let capture = try buildCapture(meaningfulSpeedChangePlan(), assessmentId: "ASSESS-42")
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertEqual(result.assessmentId, "ASSESS-42")
        XCTAssertEqual(result.sourceCaptureSchemaVersion, capture.schemaVersion)
        XCTAssertEqual(result.sourceObservationCount, capture.observations.count)
    }

    // MARK: - Dataset integration (44)

    func testToPredictionDatasetProducesOneRecordPerEventWithMatchingFields() throws {
        let capture = try buildCapture(meaningfulSpeedChangePlan())
        let result = try TouchCandidateReplayEngine.replay(capture: capture)
        let dataset = result.toPredictionDataset(timebase: .analysisSeconds)
        XCTAssertEqual(dataset.assessmentId, result.assessmentId)
        XCTAssertEqual(dataset.predictions.count, result.events.count)
        for (record, exported) in zip(dataset.predictions, result.events) {
            XCTAssertEqual(record.eventId, exported.eventId)
            XCTAssertEqual(record.timeSeconds, exported.interactionTimeSeconds)
            XCTAssertEqual(record.ballTrackId, exported.ballTrackId)
            XCTAssertEqual(record.speedChange, exported.speedChange)
            XCTAssertEqual(record.directionSimilarity, exported.directionSimilarity)
            XCTAssertEqual(record.minimumPlayerBallDistance, exported.minimumPlayerBallDistance)
        }
    }

    // MARK: - Performance sanity (Part 19) — no fragile wall-clock assertions

    func testLargeSyntheticObservationStreamReplaysWithoutCrashingAndBoundedOutput() throws {
        var observations: [TouchCandidateObservationRecord] = []
        observations.reserveCapacity(30_000)
        var t: TimeInterval = 0
        for i in 0..<30_000 {
            // Alternates far/near with a mild oscillating velocity so a
            // realistic, non-trivial number of episodes arm and resolve,
            // without being pathological.
            let near = (i / 20) % 3 == 0
            let angle = Double(i % 40) * 0.05
            let velocity = NormalizedPoint(x: 0.15 * cos(angle), y: 0.15 * sin(angle))
            observations.append(TouchCandidateObservationRecord(
                timestampSeconds: t, ballVisible: true, ballVelocity: velocity,
                normalizedPlayerBallDistance: near ? 1.0 : 3.0,
                ballProximityState: near ? .near : .away, ballTrackId: 1,
                coMovementState: near ? .sustained : .unavailable
            ))
            t += 1.0 / 15.0
        }
        let capture = try TouchCandidateObservationCapture(assessmentId: "PERF", observations: observations)

        let result = try TouchCandidateReplayEngine.replay(capture: capture)

        XCTAssertEqual(result.sourceObservationCount, 30_000)
        // Deterministic, structural bound: strictly fewer events than
        // observations (an event requires an entire multi-tick episode),
        // and never exceeds the detector's own eventCapacity default (500).
        XCTAssertLessThan(result.events.count, observations.count)
        XCTAssertLessThanOrEqual(result.events.count, 500)

        // Re-running must yield the identical count — no accidental
        // accumulation or ordering nondeterminism across runs.
        let result2 = try TouchCandidateReplayEngine.replay(capture: capture)
        XCTAssertEqual(result.events.count, result2.events.count)
    }
}
