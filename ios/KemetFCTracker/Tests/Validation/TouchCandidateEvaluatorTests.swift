//
//  TouchCandidateEvaluatorTests.swift
//  KemetFCTrackerTests
//
//  Proves TouchCandidateEvaluator's matching/metrics semantics — not merely
//  coverage. Every scenario is hand-verified against the documented DP
//  matching algorithm before being asserted here.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateEvaluatorTests: XCTestCase {
    private func groundTruth(_ touches: [(String, Double)], timebase: Timebase = .videoSeconds, assessmentId: String = "A1") -> GroundTruthDataset {
        GroundTruthDataset(assessmentId: assessmentId, timebase: timebase, touches: touches.map { GroundTruthTouch(id: $0.0, timeSeconds: $0.1) })
    }

    private func predictions(_ preds: [(String, Double)], timebase: Timebase = .videoSeconds, assessmentId: String = "A1") -> PredictionDataset {
        PredictionDataset(assessmentId: assessmentId, timebase: timebase, predictions: preds.map { TouchCandidatePredictionRecord(eventId: $0.0, timeSeconds: $0.1) })
    }

    // MARK: 1-4 — basic cardinality cases

    func testPerfectMatch() throws {
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0), ("g2", 2.0)]),
            predictions: predictions([("p1", 1.0), ("p2", 2.0)])
        )
        XCTAssertEqual(result.truePositiveCount, 2)
        XCTAssertEqual(result.falsePositiveCount, 0)
        XCTAssertEqual(result.falseNegativeCount, 0)
        XCTAssertEqual(result.precision, 1.0)
        XCTAssertEqual(result.recall, 1.0)
        XCTAssertEqual(result.f1Score ?? -1, 1.0, accuracy: 1e-9)
    }

    func testNoPredictions() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([]))
        XCTAssertEqual(result.truePositiveCount, 0)
        XCTAssertEqual(result.falseNegativeCount, 1)
        XCTAssertNil(result.precision, "undefined — never 0 — with zero predictions")
        XCTAssertEqual(result.recall, 0)
    }

    func testNoGroundTruth() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([]), predictions: predictions([("p1", 1.0)]))
        XCTAssertEqual(result.truePositiveCount, 0)
        XCTAssertEqual(result.falsePositiveCount, 1)
        XCTAssertEqual(result.precision, 0)
        XCTAssertNil(result.recall, "undefined — never 0 — with zero ground truth")
    }

    func testNeitherPredictionsNorGroundTruth() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([]), predictions: predictions([]))
        XCTAssertEqual(result.truePositiveCount, 0)
        XCTAssertEqual(result.falsePositiveCount, 0)
        XCTAssertEqual(result.falseNegativeCount, 0)
        XCTAssertNil(result.precision)
        XCTAssertNil(result.recall)
        XCTAssertNil(result.f1Score)
    }

    // MARK: 5-6 — tolerance boundary

    func testPredictionExactlyAtToleranceBoundaryMatches() throws {
        let config = TouchCandidateValidationConfiguration(matchingToleranceSeconds: 0.30)
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([("p1", 1.30)]), configuration: config
        )
        XCTAssertEqual(result.truePositiveCount, 1, "the boundary itself must be inclusive")
    }

    func testPredictionJustOutsideToleranceDoesNotMatch() throws {
        let config = TouchCandidateValidationConfiguration(matchingToleranceSeconds: 0.30)
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([("p1", 1.31)]), configuration: config
        )
        XCTAssertEqual(result.truePositiveCount, 0)
        XCTAssertEqual(result.falsePositiveCount, 1)
        XCTAssertEqual(result.falseNegativeCount, 1)
    }

    // MARK: 7-9 — competition / duplicates

    func testTwoPredictionsCompetingForOneGroundTruthOnlyTheCloserWins() throws {
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([("p1", 1.05), ("p2", 1.20)])
        )
        XCTAssertEqual(result.truePositiveCount, 1)
        XCTAssertEqual(result.matches.first?.predictionEventId, "p1")
        XCTAssertEqual(result.falsePositiveCount, 1)
    }

    func testTwoGroundTruthCompetingForOnePredictionOnlyTheCloserWins() throws {
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0), ("g2", 1.20)]), predictions: predictions([("p1", 1.05)])
        )
        XCTAssertEqual(result.truePositiveCount, 1)
        XCTAssertEqual(result.matches.first?.groundTruthId, "g1")
        XCTAssertEqual(result.falseNegativeCount, 1)
    }

    func testDuplicatePredictionsAroundOneRealTouchOnlyOneBecomesATruePositive() throws {
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0)]),
            predictions: predictions([("p1", 0.90), ("p2", 1.05), ("p3", 1.50)])
        )
        XCTAssertEqual(result.truePositiveCount, 1, "duplicate predictions around one touch must not become multiple TPs")
        XCTAssertEqual(result.matches.first?.predictionEventId, "p2", "the closer of the two eligible duplicates")
        XCTAssertEqual(result.falsePositiveCount, 2)
    }

    // MARK: 10-11 — determinism / global optimality

    func testDeterministicTieHandling() throws {
        // p1 and p2 are EXACTLY equidistant from g1.
        let gt = groundTruth([("g1", 1.0)])
        let preds = predictions([("p1", 0.95), ("p2", 1.05)])
        let first = try TouchCandidateEvaluator.evaluate(groundTruth: gt, predictions: preds)
        let second = try TouchCandidateEvaluator.evaluate(groundTruth: gt, predictions: preds)
        XCTAssertEqual(first.truePositiveCount, 1, "a tie must still resolve to exactly one match, never zero or two")
        XCTAssertEqual(first.matches.first?.predictionEventId, second.matches.first?.predictionEventId, "the SAME input must always resolve the tie the SAME way")
    }

    /// A verified, worked counterexample: greedy nearest-neighbor-per-item
    /// finds only 1 match here; the true optimum finds 2. tolerance=0.5,
    /// GT=[0.6, 1.0], P=[0.2, 0.8]. Greedy (G0=0.6 picks its true nearest,
    /// P1=0.8 at cost 0.2, since 0.2<0.4) leaves G1=1.0 with only P0=0.2
    /// remaining (cost 0.8, exceeds tolerance) → 1 match. The optimal
    /// assignment (G0↔P0 cost 0.4, G1↔P1 cost 0.2) yields 2 matches at
    /// total cost 0.6 — MORE matches at a HIGHER individual cost for G0,
    /// which is exactly what a pure "nearest neighbor" greedy can never find.
    func testGloballyOptimalAssignmentCaseWhereGreedyNearestMatchingFails() throws {
        let config = TouchCandidateValidationConfiguration(matchingToleranceSeconds: 0.5)
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 0.6), ("g2", 1.0)]),
            predictions: predictions([("p1", 0.2), ("p2", 0.8)]),
            configuration: config
        )
        XCTAssertEqual(result.truePositiveCount, 2, "the optimal assignment finds both matches; greedy nearest-neighbor would find only one")
        XCTAssertEqual(result.matches.first(where: { $0.groundTruthId == "g1" })?.predictionEventId, "p1")
        XCTAssertEqual(result.matches.first(where: { $0.groundTruthId == "g2" })?.predictionEventId, "p2")
    }

    // MARK: 12-14 — signed error / timing statistics

    func testSignedTimingErrorBeforeGroundTruthIsNegative() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 2.0)]), predictions: predictions([("p1", 1.8)]))
        XCTAssertEqual(result.matches.first?.signedTimingErrorSeconds ?? 1, -0.2, accuracy: 1e-9)
        XCTAssertEqual(result.matches.first?.absoluteTimingErrorSeconds ?? -1, 0.2, accuracy: 1e-9)
    }

    func testSignedTimingErrorAfterGroundTruthIsPositive() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 2.0)]), predictions: predictions([("p1", 2.2)]))
        XCTAssertEqual(result.matches.first?.signedTimingErrorSeconds ?? -1, 0.2, accuracy: 1e-9)
    }

    func testTimingStatisticsCalculations() throws {
        let config = TouchCandidateValidationConfiguration(matchingToleranceSeconds: 0.5)
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0), ("g2", 2.0), ("g3", 3.0)]),
            predictions: predictions([("p1", 1.1), ("p2", 1.9), ("p3", 3.3)]),
            configuration: config
        )
        XCTAssertEqual(result.truePositiveCount, 3)
        // errors: (1.1-1.0)=+0.1, (1.9-2.0)=-0.1, (3.3-3.0)=+0.3 → sum=0.3
        XCTAssertEqual(result.timing.meanSignedErrorSeconds ?? -99, 0.3 / 3, accuracy: 1e-9)
        XCTAssertEqual(result.timing.meanAbsoluteErrorSeconds ?? -99, 0.5 / 3, accuracy: 1e-9)
        XCTAssertEqual(result.timing.medianAbsoluteErrorSeconds ?? -99, 0.1, accuracy: 1e-9)
        XCTAssertEqual(result.timing.maximumAbsoluteErrorSeconds ?? -99, 0.3, accuracy: 1e-9)
    }

    // MARK: 15-17 — undefined metric cases

    func testUndefinedPrecisionCaseIsNilNotZero() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([]))
        XCTAssertNil(result.precision)
    }

    func testUndefinedRecallCaseIsNilNotZero() throws {
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([]), predictions: predictions([("p1", 1.0)]))
        XCTAssertNil(result.recall)
    }

    func testUndefinedF1CaseWhenPrecisionAndRecallAreBothDefinedButZero() throws {
        let config = TouchCandidateValidationConfiguration(matchingToleranceSeconds: 0.3)
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([("p1", 5.0)]), configuration: config
        )
        XCTAssertEqual(result.precision, 0, "precision IS defined here (1 prediction exists) — just 0")
        XCTAssertEqual(result.recall, 0, "recall IS defined here (1 ground truth exists) — just 0")
        XCTAssertNil(result.f1Score, "0/0 in the F1 formula must be nil, never fabricated as 0")
    }

    // MARK: 18-20 — timestamp mapping

    func testTimestampMappingAppliedCorrectly() throws {
        let mapping = TimestampMapping(offsetSeconds: 0.2, provenance: .approximatedFromHistoricalMeasurement)
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 10.0)], timebase: .videoSeconds),
            predictions: predictions([("p1", 9.8)], timebase: .analysisSeconds),
            mapping: mapping
        )
        XCTAssertEqual(result.truePositiveCount, 1)
        XCTAssertEqual(result.matches.first?.predictedTimeSeconds ?? -1, 10.0, accuracy: 1e-9)
        XCTAssertEqual(result.matches.first?.signedTimingErrorSeconds ?? -1, 0, accuracy: 1e-9)
        XCTAssertEqual(result.timestampMapping.offsetSeconds, 0.2)
        XCTAssertEqual(result.timestampMapping.provenance, .approximatedFromHistoricalMeasurement)
    }

    func testMissingTimestampMappingIsRejectedWhenTimebasesDiffer() {
        XCTAssertThrowsError(try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 10.0)], timebase: .videoSeconds),
            predictions: predictions([("p1", 9.8)], timebase: .analysisSeconds)
            // mapping defaults to .identical — must be rejected, not silently applied.
        )) { error in
            XCTAssertEqual(error as? TouchCandidateValidationError, .missingTimestampMapping(groundTruthTimebase: .videoSeconds, predictionTimebase: .analysisSeconds))
        }
    }

    func testIdenticalTimebasesRequireNoMapping() throws {
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 10.0)], timebase: .videoSeconds),
            predictions: predictions([("p1", 10.1)], timebase: .videoSeconds)
        )
        XCTAssertEqual(result.truePositiveCount, 1)
    }

    // MARK: 21-24 — input robustness

    func testMalformedGroundTruthTimestampsStillMatchCorrectly() throws {
        // A negative timestamp is unusual but not itself invalid data — the
        // evaluator must not crash or misbehave on it.
        let result = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", -0.1)]), predictions: predictions([("p1", -0.05)])
        )
        XCTAssertEqual(result.truePositiveCount, 1)
    }

    func testDuplicateGroundTruthIdsAreRejected() {
        XCTAssertThrowsError(try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0), ("g1", 2.0)]), predictions: predictions([])
        )) { error in
            XCTAssertEqual(error as? TouchCandidateValidationError, .duplicateGroundTruthId("g1"))
        }
    }

    func testDuplicatePredictionIdsAreRejected() {
        XCTAssertThrowsError(try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([]), predictions: predictions([("p1", 1.0), ("p1", 2.0)])
        )) { error in
            XCTAssertEqual(error as? TouchCandidateValidationError, .duplicatePredictionId("p1"))
        }
    }

    func testUnsortedInputProducesTheSameResultAsSortedInput() throws {
        let sortedResult = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g1", 1.0), ("g2", 2.0)]),
            predictions: predictions([("p1", 1.05), ("p2", 2.05)])
        )
        let unsortedResult = try TouchCandidateEvaluator.evaluate(
            groundTruth: groundTruth([("g2", 2.0), ("g1", 1.0)]),
            predictions: predictions([("p2", 2.05), ("p1", 1.05)])
        )
        XCTAssertEqual(sortedResult.truePositiveCount, unsortedResult.truePositiveCount)
        XCTAssertEqual(Set(sortedResult.matches.map { $0.groundTruthId }), Set(unsortedResult.matches.map { $0.groundTruthId }))
    }

    // MARK: 25-27 — aggregation

    func testAggregationAcrossMultipleSessions() throws {
        let session1 = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([("p1", 1.0)]))
        let session2 = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([]))
        let aggregate = TouchCandidateEvaluator.aggregate([session1, session2])
        XCTAssertEqual(aggregate.sessionCount, 2)
        XCTAssertEqual(aggregate.truePositiveCount, 1)
        XCTAssertEqual(aggregate.falseNegativeCount, 1)
        XCTAssertEqual(aggregate.groundTruthCount, 2)
    }

    func testAggregationUsesTotalCountsRatherThanAveragingPerSessionMetrics() throws {
        // Session 1: perfect (TP=1, FP=0) → precision 1.0
        let session1 = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([("p1", 1.0)]))
        // Session 2: TP=1, FP=9 → precision 0.1
        var farPredictions: [(String, Double)] = [("p1", 100.0)]
        for i in 0..<9 { farPredictions.append(("noise\(i)", 200.0 + Double(i))) }
        let session2 = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 100.0)]), predictions: predictions(farPredictions))
        XCTAssertEqual(session2.truePositiveCount, 1)
        XCTAssertEqual(session2.falsePositiveCount, 9)

        let aggregate = TouchCandidateEvaluator.aggregate([session1, session2])
        // Pooled: TP=2, FP=9 → precision = 2/11 ≈ 0.1818. NOT the average of
        // 1.0 and 0.1 (which would be 0.55).
        XCTAssertEqual(aggregate.precision ?? -1, 2.0 / 11.0, accuracy: 1e-9)
        XCTAssertNotEqual(aggregate.precision ?? -1, 0.55, accuracy: 0.01)
    }

    func testNoMatchedEventsAcrossAnySessionMeansTimingMetricsAreUnavailable() throws {
        let session = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth([("g1", 1.0)]), predictions: predictions([]))
        XCTAssertEqual(session.timing, .unavailable)
        let aggregate = TouchCandidateEvaluator.aggregate([session])
        XCTAssertEqual(aggregate.timing, .unavailable, "must be unavailable, never zero-filled")
    }

    // MARK: - Prediction-record projection (Part 3 — reuse, not duplication)

    func testPredictionRecordIsAMechanicalProjectionOfALiveTouchCandidateEvent() {
        let evidence = TouchCandidateEvidence(
            preBallSpeed: 0.2, postBallSpeed: 0.4, speedChange: 0.2,
            directionSimilarity: 0.9, minimumPlayerBallDistance: 0.8,
            coMovementBefore: .notSupported, coMovementAfter: .candidate,
            preSampleCount: 5, postSampleCount: 5
        )
        let event = TouchCandidateEvent(startTime: 1.0, interactionTime: 1.5, endTime: 2.0, ballTrackId: 7, evidence: evidence)
        let record = TouchCandidatePredictionRecord(from: event)
        XCTAssertEqual(record.eventId, event.id.uuidString)
        XCTAssertEqual(record.timeSeconds, event.interactionTime, "per the audit, interactionTime is the predicted interaction instant")
        XCTAssertEqual(record.ballTrackId, 7)
        XCTAssertEqual(record.speedChange, 0.2)
        XCTAssertEqual(record.directionSimilarity, 0.9)
        XCTAssertEqual(record.minimumPlayerBallDistance, 0.8)
    }

    // MARK: - JSON determinism (Part 2 — "human-editable... easy to diff in Git")

    func testGroundTruthDatasetRoundTripsLosslesslyThroughJSON() throws {
        let dataset = GroundTruthDataset(
            assessmentId: "A123", videoFile: "clip.mov", timebase: .videoSeconds,
            touches: [GroundTruthTouch(id: "touch-001", timeSeconds: 12.43, notes: "clean pass")]
        )
        let data = try TouchCandidateValidationIO.encoder().encode(dataset)
        let decoded = try TouchCandidateValidationIO.decoder().decode(GroundTruthDataset.self, from: data)
        XCTAssertEqual(decoded, dataset)
        // Re-encoding must be byte-identical (deterministic, diff-friendly).
        let data2 = try TouchCandidateValidationIO.encoder().encode(decoded)
        XCTAssertEqual(data, data2)
    }
}
