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

    func testInvalidConfigurationReplayIsRejectedNotSilentlySubstituted() throws {
        let capture = try buildCapture(noEventPlan())
        var invalid = TouchCandidateDetectorConfigurationSnapshot.baseline
        invalid.cooldownDurationSeconds = -1.0
        XCTAssertThrowsError(try TouchCandidateReplayEngine.replay(capture: capture, configuration: invalid)) { error in
            XCTAssertEqual(error as? TouchCandidateReplayError, .invalidConfiguration(.negativeDuration(field: "cooldownDurationSeconds")))
        }
    }

    func testValidNonBaselineConfigurationReplayIsAccepted() throws {
        // A semantically valid, non-baseline configuration must now be
        // ACCEPTED (2026-09-28, configuration injection) — the old
        // baseline-only restriction is gone.
        let capture = try buildCapture(noEventPlan())
        var lenient = TouchCandidateDetectorConfigurationSnapshot.baseline
        lenient.minimumSpeedChangeForTouchCandidate = 0.001
        XCTAssertNoThrow(try TouchCandidateReplayEngine.replay(capture: capture, configuration: lenient))
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

    // MARK: - Part 8: controlled alternate-configuration behavior change

    func testBorderlineSpeedChangeRejectedByBaselineEmittedByLowerThreshold() throws {
        // Same direction throughout (cosine == 1) so direction-change never
        // contributes — isolates the speed-change threshold as the ONLY
        // variable under test.
        var plan: TickPlan = []
        let pre = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, pre, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, pre, true, 1.0, .candidate, 1))
        let post = NormalizedPoint(x: 0.25, y: 0) // |speedChange| = 0.05: below baseline 0.08, above a lower test threshold
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, post, true, 1.0, .sustained, 1)) }
        let capture = try buildCapture(plan)

        let baselineResult = try TouchCandidateReplayEngine.replay(capture: capture, configuration: .baseline)
        XCTAssertTrue(baselineResult.events.isEmpty, "baseline (threshold 0.08) must reject a 0.05 speed change")

        var lowerThreshold = TouchCandidateDetectorConfigurationSnapshot.baseline
        lowerThreshold.minimumSpeedChangeForTouchCandidate = 0.03
        let alternateResult = try TouchCandidateReplayEngine.replay(capture: capture, configuration: lowerThreshold)
        XCTAssertEqual(alternateResult.events.count, 1, "lower threshold (0.03) must emit the SAME 0.05 speed change")
        XCTAssertEqual(alternateResult.events.first?.speedChange ?? -99, 0.05, accuracy: 1e-9)
    }

    func testBorderlineDirectionChangeRejectedByBaselineEmittedByAlternateMaximum() throws {
        var plan: TickPlan = []
        let pre = NormalizedPoint(x: 0.3, y: 0) // speed 0.3
        for i in 0..<5 { plan.append((armTime(i), .away, pre, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, pre, true, 1.0, .candidate, 1))
        // Same magnitude (0.3), rotated ~53.13° — cosine similarity == 0.6,
        // so |speedChange| == 0 (never meaningful) and only direction can
        // decide the outcome.
        let post = NormalizedPoint(x: 0.18, y: 0.24)
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, post, true, 1.0, .sustained, 1)) }
        let capture = try buildCapture(plan)

        let baselineResult = try TouchCandidateReplayEngine.replay(capture: capture, configuration: .baseline)
        XCTAssertTrue(baselineResult.events.isEmpty, "baseline (max similarity 0.5) must reject a 0.6-similarity direction change")

        var higherMaximum = TouchCandidateDetectorConfigurationSnapshot.baseline
        higherMaximum.maximumDirectionSimilarityForTouchCandidate = 0.7
        let alternateResult = try TouchCandidateReplayEngine.replay(capture: capture, configuration: higherMaximum)
        XCTAssertEqual(alternateResult.events.count, 1, "higher maximum (0.7) must emit the SAME 0.6-similarity direction change")
        XCTAssertEqual(alternateResult.events.first?.directionSimilarity ?? -99, 0.6, accuracy: 1e-9)
    }

    func testAlternateConfigurationNeverLabeledCalibratedOrSuperior() throws {
        // Structural guard, not a semantic one: `.baseline` itself must
        // remain untouched by constructing/using an alternate configuration
        // elsewhere in the same process (Part 13 — no calibration performed).
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.001
        _ = try TouchCandidateReplayEngine.replay(capture: buildCapture(noEventPlan()), configuration: alternate)
        XCTAssertEqual(TouchCandidateDetectorConfigurationSnapshot.baseline.minimumSpeedChangeForTouchCandidate, TouchCandidateCalibration.minimumSpeedChangeForTouchCandidate, "using an alternate configuration for replay must never mutate .baseline or the live TouchCandidateCalibration constants")
    }

    // MARK: - Part 10: determinism across configurations

    func testReplayIsDeterministicAcrossRepeatedRunsWithAlternateConfiguration() throws {
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.03
        let capture = try buildCapture(meaningfulSpeedChangePlan())
        let result1 = try TouchCandidateReplayEngine.replay(capture: capture, configuration: alternate)
        let result2 = try TouchCandidateReplayEngine.replay(capture: capture, configuration: alternate)
        assertPredictionsEquivalent(result1.events, result2.events)
        XCTAssertEqual(result1.configurationFingerprint, result2.configurationFingerprint)
    }

    func testSameObservationsSameFingerprintProduceIdenticalEvents() throws {
        let capture = try buildCapture(meaningfulSpeedChangePlan())
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.03
        let result1 = try TouchCandidateReplayEngine.replay(capture: capture, configuration: alternate)
        // Reconstruct an equal-but-distinct configuration value (not the
        // same Swift value in memory) with the identical fingerprint.
        var reconstructed = TouchCandidateDetectorConfigurationSnapshot.baseline
        reconstructed.minimumSpeedChangeForTouchCandidate = 0.03
        XCTAssertEqual(alternate.fingerprint, reconstructed.fingerprint)
        let result2 = try TouchCandidateReplayEngine.replay(capture: capture, configuration: reconstructed)
        assertPredictionsEquivalent(result1.events, result2.events)
    }

    // MARK: - Part 11: capture-vs-replay provenance stay distinct

    func testCaptureConfigurationAtCaptureNeverOverwrittenByReplayConfiguration() throws {
        let observations = meaningfulSpeedChangePlan().map { step in
            TouchCandidateObservationRecord(
                timestampSeconds: step.t, ballVisible: step.visible, ballVelocity: step.velocity,
                normalizedPlayerBallDistance: step.distance, ballProximityState: step.proximity,
                ballTrackId: step.visible ? step.trackId : nil, coMovementState: step.coMovement
            )
        }
        let capture = try TouchCandidateObservationCapture(assessmentId: "A1", detectorConfigurationAtCapture: .baseline, observations: observations)
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.03

        let result = try TouchCandidateReplayEngine.replay(capture: capture, configuration: alternate)

        // "Captured while live configuration = baseline; replayed using
        // configuration = alternate" — unambiguous, without mutating the
        // capture's own historical provenance.
        XCTAssertEqual(capture.detectorConfigurationAtCapture, .baseline)
        XCTAssertEqual(capture.detectorConfigurationFingerprint, TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)
        XCTAssertEqual(result.replayDetectorConfiguration, alternate)
        XCTAssertEqual(result.configurationFingerprint, alternate.fingerprint)
        XCTAssertNotEqual(result.configurationFingerprint, capture.detectorConfigurationFingerprint)
    }

    // MARK: - Part 12: dataset/experiment integration under an alternate configuration

    func testAlternateConfigurationReplayConvertsToPredictionDatasetUnmodified() throws {
        var lowerThreshold = TouchCandidateDetectorConfigurationSnapshot.baseline
        lowerThreshold.minimumSpeedChangeForTouchCandidate = 0.03
        var plan: TickPlan = []
        let pre = NormalizedPoint(x: 0.2, y: 0)
        for i in 0..<5 { plan.append((armTime(i), .away, pre, true, 3.0, .unavailable, 1)) }
        plan.append((0.5, .near, pre, true, 1.0, .candidate, 1))
        let post = NormalizedPoint(x: 0.25, y: 0)
        for i in 1...5 { plan.append((0.5 + Double(i) * 0.1, .near, post, true, 1.0, .sustained, 1)) }
        let capture = try buildCapture(plan)

        let result = try TouchCandidateReplayEngine.replay(capture: capture, configuration: lowerThreshold)
        XCTAssertEqual(result.events.count, 1, "sanity: this plan must emit under the lower threshold")
        let dataset = result.toPredictionDataset(timebase: .analysisSeconds)
        XCTAssertEqual(dataset.predictions.count, 1)
        XCTAssertEqual(dataset.predictions.first?.speedChange, result.events.first?.speedChange)
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

    func testLargeSyntheticObservationStreamReplaysUnderAnAlternateConfigurationWithoutCrashing() throws {
        var observations: [TouchCandidateObservationRecord] = []
        observations.reserveCapacity(30_000)
        var t: TimeInterval = 0
        for i in 0..<30_000 {
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
        let capture = try TouchCandidateObservationCapture(assessmentId: "PERF-ALT", observations: observations)
        var lenient = TouchCandidateDetectorConfigurationSnapshot.baseline
        lenient.minimumSpeedChangeForTouchCandidate = 0.01
        lenient.maximumDirectionSimilarityForTouchCandidate = 0.9

        let start = Date()
        let result = try TouchCandidateReplayEngine.replay(capture: capture, configuration: lenient)
        let elapsed = Date().timeIntervalSince(start)

        XCTAssertEqual(result.sourceObservationCount, 30_000)
        XCTAssertLessThan(result.events.count, observations.count)
        XCTAssertLessThanOrEqual(result.events.count, 500)
        // Diagnostic only (Part 14) — not a pass/fail gate, since device
        // performance varies; printed for visibility.
        print("[perf] 30,000-observation alternate-config replay took \(elapsed)s, \(result.events.count) events")
    }
}
