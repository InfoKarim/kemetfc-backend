//
//  TouchCandidateExperimentTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 19's SPLIT SAFETY, CONFIGURATION, EXPERIMENT, and COMPARISON
//  items.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateExperimentTests: XCTestCase {
    private func entry(
        _ id: String, split: DatasetSplit, sync: SynchronizationProvenance = .measuredFromCapturePTS,
        inclusion: AssessmentInclusionStatus = .included
    ) -> TouchCandidateDatasetAssessmentEntry {
        TouchCandidateDatasetAssessmentEntry(assessmentId: id, synchronizationStatus: sync, split: split, inclusionStatus: inclusion)
    }

    private func config(
        purpose: TouchCandidateExperimentPurpose, allowedSplits: [DatasetSplit],
        requiredSync: SynchronizationProvenance = .unavailable
    ) -> TouchCandidateExperimentConfiguration {
        TouchCandidateExperimentConfiguration(
            experimentId: "E1", datasetId: "D1", purpose: purpose, allowedSplits: allowedSplits,
            requiredSynchronizationQuality: requiredSync, matchingToleranceSeconds: 0.3
        )
    }

    private func gt(_ id: String, _ t: Double = 1.0) -> GroundTruthDataset {
        GroundTruthDataset(assessmentId: id, timebase: .analysisSeconds, touches: [GroundTruthTouch(id: "g1", timeSeconds: t)])
    }

    private func preds(_ id: String, _ t: Double = 1.0) -> PredictionDataset {
        PredictionDataset(assessmentId: id, timebase: .analysisSeconds, predictions: [TouchCandidatePredictionRecord(eventId: "p1", timeSeconds: t)])
    }

    // MARK: Split safety (9-14)

    func testDevelopmentExperimentAcceptsDevelopment() throws {
        try config(purpose: .developmentEvaluation, allowedSplits: [.development]).validateSplitSafety()
    }

    func testHoldoutExperimentAcceptsHoldoutWhenEvaluationModeAllowsIt() throws {
        try config(purpose: .holdoutEvaluation, allowedSplits: [.holdout]).validateSplitSafety()
    }

    func testCalibrationOperationRejectsHoldout() {
        XCTAssertThrowsError(try config(purpose: .calibration, allowedSplits: [.calibration, .holdout]).validateSplitSafety()) { error in
            XCTAssertEqual(error as? TouchCandidateExperimentError, .holdoutNotAllowedInCalibration)
        }
    }

    func testMixedDevelopmentAndHoldoutCalibrationRejected() {
        XCTAssertThrowsError(try config(purpose: .calibration, allowedSplits: [.development, .holdout]).validateSplitSafety()) { error in
            XCTAssertEqual(error as? TouchCandidateExperimentError, .holdoutNotAllowedInCalibration)
        }
    }

    func testHoldoutEvaluationRejectsNonHoldoutSplits() {
        XCTAssertThrowsError(try config(purpose: .holdoutEvaluation, allowedSplits: [.holdout, .development]).validateSplitSafety()) { error in
            XCTAssertEqual(error as? TouchCandidateExperimentError, .nonHoldoutSplitInHoldoutEvaluation)
        }
    }

    func testExcludedAssessmentNeverSilentlyIncluded() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, inclusion: .excluded(reason: .manualExclusion))
        ])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.sessionCount, 0)
        XCTAssertEqual(result.excludedAssessments, [ExcludedAssessment(assessmentId: "A1", reason: .manualExclusion)])
    }

    // MARK: Configuration (15-19)

    func testBaselineConfigurationSnapshotEqualsCurrentConstants() {
        let baseline = TouchCandidateDetectorConfigurationSnapshot.baseline
        XCTAssertEqual(baseline.preWindowSeconds, TouchCandidateCalibration.preWindowSeconds)
        XCTAssertEqual(baseline.postWindowSeconds, TouchCandidateCalibration.postWindowSeconds)
        XCTAssertEqual(baseline.cooldownDurationSeconds, TouchCandidateCalibration.cooldownDurationSeconds)
        XCTAssertEqual(baseline.dropoutGraceDurationSeconds, TouchCandidateCalibration.dropoutGraceDurationSeconds)
        XCTAssertEqual(baseline.minimumSpeedChangeForTouchCandidate, TouchCandidateCalibration.minimumSpeedChangeForTouchCandidate)
        XCTAssertEqual(baseline.maximumDirectionSimilarityForTouchCandidate, TouchCandidateCalibration.maximumDirectionSimilarityForTouchCandidate)
        XCTAssertEqual(baseline.minimumSpeedForMeaningfulDirection, TouchCandidateCalibration.minimumSpeedForMeaningfulDirection)
    }

    func testIdenticalConfigurationProducesIdenticalFingerprint() {
        let a = TouchCandidateDetectorConfigurationSnapshot.baseline
        let b = TouchCandidateDetectorConfigurationSnapshot.baseline
        XCTAssertEqual(a.fingerprint, b.fingerprint)
    }

    func testOneThresholdChangeProducesADifferentFingerprint() {
        let baseline = TouchCandidateDetectorConfigurationSnapshot.baseline
        var changed = baseline
        changed.minimumSpeedChangeForTouchCandidate += 0.001
        XCTAssertNotEqual(baseline.fingerprint, changed.fingerprint)
    }

    func testCanonicalOrderingDoesNotAffectFingerprint() {
        // Two snapshots built with arguments in a DIFFERENT call order must
        // still hash identically — fingerprint is derived from a fixed
        // field layout, never argument/JSON ordering.
        let a = TouchCandidateDetectorConfigurationSnapshot(
            version: "v1", preWindowSeconds: 0.5, postWindowSeconds: 0.5, cooldownDurationSeconds: 0.5,
            dropoutGraceDurationSeconds: 0.5, minimumSpeedChangeForTouchCandidate: 0.08,
            maximumDirectionSimilarityForTouchCandidate: 0.5, minimumSpeedForMeaningfulDirection: 0.04
        )
        let data = try! TouchCandidateValidationIO.encoder().encode(a)
        let decoded = try! TouchCandidateValidationIO.decoder().decode(TouchCandidateDetectorConfigurationSnapshot.self, from: data)
        XCTAssertEqual(a.fingerprint, decoded.fingerprint, "round-tripping through JSON (which may reorder keys) must not change the fingerprint")
    }

    func testDetectorAndEvaluatorConfigurationsRemainSeparateTypes() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        // The experiment result carries a detector fingerprint/version
        // (from TouchCandidateDetectorConfigurationSnapshot) AND a separate
        // evaluationConfiguration (TouchCandidateValidationConfiguration,
        // matching tolerance only) — never merged into one type.
        XCTAssertEqual(result.detectorConfigurationFingerprint, TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)
        XCTAssertEqual(result.evaluationConfiguration.matchingToleranceSeconds, 0.3)
    }

    // MARK: Experiment (20-30)

    func testOneAssessmentExperiment() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.sessionCount, 1)
        XCTAssertEqual(result.aggregate.truePositiveCount, 1)
    }

    func testMultiAssessmentAggregation() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development), entry("A2", split: .development),
        ])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline,
            loadGroundTruth: { self.gt($0.assessmentId) }, loadPredictions: { self.preds($0.assessmentId) }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.sessionCount, 2)
        XCTAssertEqual(result.aggregate.truePositiveCount, 2)
    }

    func testMicroPrecisionCalculatedFromTotalCounts() throws {
        // Session 1: TP=1 FP=0 (precision 1.0). Session 2: TP=1 FP=9 (precision 0.1).
        var farPreds: [TouchCandidatePredictionRecord] = [TouchCandidatePredictionRecord(eventId: "p0", timeSeconds: 100.0)]
        for i in 0..<9 { farPreds.append(TouchCandidatePredictionRecord(eventId: "noise\(i)", timeSeconds: 500.0 + Double(i))) }
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development), entry("A2", split: .development),
        ])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline,
            loadGroundTruth: { entry in entry.assessmentId == "A1" ? self.gt("A1") : GroundTruthDataset(assessmentId: "A2", timebase: .analysisSeconds, touches: [GroundTruthTouch(id: "g1", timeSeconds: 100.0)]) },
            loadPredictions: { entry in entry.assessmentId == "A1" ? self.preds("A1") : PredictionDataset(assessmentId: "A2", timebase: .analysisSeconds, predictions: farPreds) },
            loadMapping: { _ in .identical }
        )
        // Pooled: TP=2, FP=9 → 2/11, NOT the average of 1.0 and 0.1 (0.55).
        XCTAssertEqual(result.aggregate.precision ?? -1, 2.0 / 11.0, accuracy: 1e-9)
    }

    func testMicroRecallCalculatedFromTotalCounts() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") },
            loadPredictions: { _ in PredictionDataset(assessmentId: "A1", timebase: .analysisSeconds, predictions: []) },
            loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.recall, 0)
    }

    func testMicroF1Correct() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.f1Score ?? -1, 1.0, accuracy: 1e-9)
    }

    func testUndefinedDenominatorsRemainNil() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in nil }, loadPredictions: { _ in nil }, loadMapping: { _ in .identical }
        )
        XCTAssertNil(result.aggregate.precision)
        XCTAssertNil(result.aggregate.recall)
        XCTAssertNil(result.aggregate.f1Score)
    }

    func testExclusionsReportedWithReasons() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in nil }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.excludedAssessments, [ExcludedAssessment(assessmentId: "A1", reason: .missingGroundTruth)])
    }

    func testSynchronizationEligibilityEnforced() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development, sync: .unavailable)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest,
            configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development], requiredSync: .measuredFromCapturePTS),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.excludedAssessments, [ExcludedAssessment(assessmentId: "A1", reason: .missingSynchronization)])
    }

    func testApproximateSyncRejectedByStrictExperiment() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development, sync: .approximateHistoricalOffset)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest,
            configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development], requiredSync: .measuredFromCapturePTS),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.sessionCount, 0, "an approximate offset must not satisfy a strict (measured-required) experiment")
    }

    func testMatchingToleranceRecorded() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in nil }, loadPredictions: { _ in nil }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.evaluationConfiguration.matchingToleranceSeconds, 0.3)
    }

    func testPerAssessmentResultsRetained() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1") }, loadPredictions: { _ in self.preds("A1") }, loadMapping: { _ in .identical }
        )
        XCTAssertEqual(result.aggregate.perSession.first?.assessmentId, "A1")
    }

    // MARK: Comparison (31-33)

    func testFactualMetricDeltasCorrect() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let baselineResult = try TouchCandidateExperimentRunner.run(
            experimentId: "baseline", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1", 1.0) }, loadPredictions: { _ in self.preds("A1", 1.0) }, loadMapping: { _ in .identical }
        )
        let candidateResult = try TouchCandidateExperimentRunner.run(
            experimentId: "candidate", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in self.gt("A1", 1.0) }, loadPredictions: { _ in PredictionDataset(assessmentId: "A1", timebase: .analysisSeconds, predictions: []) }, loadMapping: { _ in .identical }
        )
        let comparison = TouchCandidateExperimentComparator.compare(baseline: baselineResult, candidate: candidateResult)
        XCTAssertEqual(comparison.deltaTruePositives, -1)
        XCTAssertEqual(comparison.deltaFalseNegatives, 1)
    }

    func testNoAutomaticWinnerFieldExists() throws {
        // Structural proof: TouchCandidateExperimentComparison's Codable
        // JSON representation contains only factual delta keys — encode it
        // and confirm no "winner"/"better"/"recommended" key is present.
        let comparison = TouchCandidateExperimentComparison(
            baselineExperimentId: "b", candidateExperimentId: "c", deltaTruePositives: 0,
            deltaFalsePositives: 0, deltaFalseNegatives: 0, deltaPrecision: nil, deltaRecall: nil, deltaF1: nil, deltaMeanAbsoluteTimingErrorSeconds: nil
        )
        let data = try TouchCandidateValidationIO.encoder().encode(comparison)
        let json = String(data: data, encoding: .utf8) ?? ""
        for forbidden in ["winner", "better", "recommended", "best"] {
            XCTAssertFalse(json.lowercased().contains(forbidden), "must never contain a '\(forbidden)' field")
        }
    }

    func testUndefinedMetricDeltaRemainsNilWhenAppropriate() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let noData = try TouchCandidateExperimentRunner.run(
            experimentId: "E1", manifest: manifest, configuration: config(purpose: .developmentEvaluation, allowedSplits: [.development]),
            detectorConfiguration: .baseline, loadGroundTruth: { _ in nil }, loadPredictions: { _ in nil }, loadMapping: { _ in .identical }
        )
        let comparison = TouchCandidateExperimentComparator.compare(baseline: noData, candidate: noData)
        XCTAssertNil(comparison.deltaPrecision)
        XCTAssertNil(comparison.deltaRecall)
        XCTAssertNil(comparison.deltaF1)
    }
}
