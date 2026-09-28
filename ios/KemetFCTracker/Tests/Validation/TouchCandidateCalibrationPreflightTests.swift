//
//  TouchCandidateCalibrationPreflightTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 16's SPLITS (8-12), LEAKAGE (13-15), SYNC (16-18),
//  CONFIGURATION (19-21), EXCLUSIONS (22-26), and the preflight-specific
//  portion of REPRODUCIBILITY (27-28) items.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateCalibrationPreflightTests: XCTestCase {
    private func entry(
        _ id: String, split: DatasetSplit, sync: SynchronizationProvenance = .approximateHistoricalOffset,
        inclusion: AssessmentInclusionStatus = .included, groupId: String? = nil
    ) -> TouchCandidateDatasetAssessmentEntry {
        TouchCandidateDatasetAssessmentEntry(assessmentId: id, synchronizationStatus: sync, split: split, inclusionStatus: inclusion, groupId: groupId)
    }

    private func fullyAvailable(_ entry: TouchCandidateDatasetAssessmentEntry) -> TouchCandidateArtifactAvailability {
        TouchCandidateArtifactAvailability(groundTruthAvailable: true, predictionsAvailable: true, observationCaptureAvailable: false)
    }

    private func protocolValue(
        category: TouchCandidateExperimentCategory = .exploratoryDevelopment,
        allowedSplits: [DatasetSplit] = [.development, .calibration],
        leakagePolicy: TouchCandidateLeakagePolicy = .forbidCrossSplitGroupSharing,
        holdoutPolicy: TouchCandidateHoldoutPolicy = .sealed
    ) -> TouchCandidateCalibrationProtocol {
        TouchCandidateCalibrationProtocol(
            protocolId: "PROTO-1", datasetId: "D1", category: category, allowedSplits: allowedSplits,
            minimumSynchronizationProvenance: category.minimumSynchronizationProvenance, metricPolicy: .standard,
            matchingToleranceSeconds: 0.3, candidateConfigurationPolicy: TouchCandidateCandidateConfigurationSet(configurations: [.baseline]),
            holdoutPolicy: holdoutPolicy, leakagePolicy: leakagePolicy
        )
    }

    // MARK: Splits (8-12)

    func testDevelopmentAllowedWhenProtocolPermits() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable
        )
        XCTAssertTrue(result.passed)
        XCTAssertEqual(result.eligibleAssessmentIds, ["A1"])
    }

    func testCalibrationAllowedWhenProtocolPermits() throws {
        // .calibrationQuality requires .derivedFromRecordedAsset sync (rank
        // 2) — supply it explicitly so THIS test isolates split permission,
        // not sync gating (that is covered separately under Sync 16-18).
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .calibration, sync: .derivedFromRecordedAsset)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(category: .calibrationQuality, allowedSplits: [.calibration]),
            manifest: manifest, requestedSplits: [.calibration], artifactAvailability: fullyAvailable
        )
        XCTAssertTrue(result.passed)
        XCTAssertEqual(result.eligibleAssessmentIds, ["A1"])
    }

    func testHoldoutStructurallyCannotBeRequestedFromOrdinaryPreflight() {
        // `TouchCandidateNonHoldoutSplit` has exactly two cases — `.development`
        // and `.calibration` — this is a compile-time fact, verified here by
        // enumerating every case and confirming `.holdout` is absent.
        let allPossibleRequests = TouchCandidateNonHoldoutSplit.allCases.map { $0.asDatasetSplit }
        XCTAssertFalse(allPossibleRequests.contains(.holdout), "the ordinary preflight's own parameter type must never be able to represent a holdout request")
        XCTAssertEqual(Set(allPossibleRequests), [.development, .calibration])
    }

    func testDedicatedHoldoutModeIsAStructurallySeparateFunction() throws {
        // `TouchCandidateHoldoutGate.runFinalHoldoutEvaluation` is a
        // DIFFERENT function, in a different type, with a different error
        // type and a single-configuration (not list) parameter — verified
        // here by actually invoking it end-to-end, completely independent
        // of `TouchCandidateCalibrationPreflight`.
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("H1", split: .holdout, sync: .measuredFromCapturePTS),
        ])
        let sealedResult = protocolValue(
            category: .finalHoldoutEvaluation, allowedSplits: [.holdout],
            holdoutPolicy: .unsealedForFinalEvaluation(frozenConfigurationFingerprint: TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)
        )
        let result = try TouchCandidateHoldoutGate.runFinalHoldoutEvaluation(
            protocol: sealedResult, manifest: manifest, frozenConfiguration: .baseline, artifactAvailability: fullyAvailable
        )
        XCTAssertEqual(result.eligibleAssessmentIds, ["H1"])
    }

    func testMixedSplitRequestRejectedWhenNotPermittedByProtocol() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .calibration)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(allowedSplits: [.development]), // calibration NOT permitted
            manifest: manifest, requestedSplits: [.development, .calibration], artifactAvailability: fullyAvailable
        )
        XCTAssertFalse(result.passed)
        XCTAssertEqual(result.gateFailures, [.requestedSplitNotPermittedByProtocol(.calibration)])
    }

    // MARK: Leakage (13-15)

    func testSameGroupAcrossDevelopmentAndCalibrationRejected() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, groupId: "session-9"),
            entry("A2", split: .calibration, groupId: "session-9"),
        ])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development, .calibration], artifactAvailability: fullyAvailable
        )
        XCTAssertFalse(result.passed)
        XCTAssertEqual(result.gateFailures, [.crossSplitGroupLeakageDetected(["session-9"])])
    }

    func testSameGroupWithinSamePermittedSplitHandledCorrectly() throws {
        // Two entries sharing a groupId WITHIN the same split is not
        // cross-split leakage — both remain eligible.
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, groupId: "session-9"),
            entry("A2", split: .development, groupId: "session-9"),
        ])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable
        )
        XCTAssertTrue(result.passed)
        XCTAssertEqual(Set(result.eligibleAssessmentIds), ["A1", "A2"])
    }

    func testMissingGroupIdNeverFalselyFlaggedAsLeakage() throws {
        // Per the current audit: groupId is nil for every real assessment
        // today (no player/session-level grouping identifier exists yet) —
        // this must never be treated as if every nil-groupId entry belongs
        // to one giant shared group.
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, groupId: nil),
            entry("A2", split: .calibration, groupId: nil),
        ])
        XCTAssertTrue(manifest.crossSplitGroupLeakage().isEmpty)
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development, .calibration], artifactAvailability: fullyAvailable
        )
        XCTAssertTrue(result.passed)
        XCTAssertEqual(Set(result.eligibleAssessmentIds), ["A1", "A2"])
    }

    // MARK: Sync (16-18)

    func testInsufficientProvenanceRejected() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development, sync: .unavailable)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(category: .exploratoryDevelopment), // requires .approximateHistoricalOffset (rank 1)
            manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable
        )
        XCTAssertTrue(result.passed)
        XCTAssertEqual(result.excluded, [TouchCandidatePreflightExclusion(assessmentId: "A1", reason: .missingSynchronization)])
    }

    func testSufficientProvenanceAccepted() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development, sync: .approximateHistoricalOffset)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(category: .exploratoryDevelopment),
            manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable
        )
        XCTAssertEqual(result.eligibleAssessmentIds, ["A1"])
    }

    func testApproximateProvenanceNotSilentlyUpgradedForCalibrationQuality() throws {
        // .calibrationQuality requires .derivedFromRecordedAsset (rank 2) —
        // an approximate offset (rank 1) must NEVER satisfy it, no matter
        // how plausible it looks.
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .calibration, sync: .approximateHistoricalOffset)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(category: .calibrationQuality, allowedSplits: [.calibration]),
            manifest: manifest, requestedSplits: [.calibration], artifactAvailability: fullyAvailable
        )
        XCTAssertEqual(result.excluded, [TouchCandidatePreflightExclusion(assessmentId: "A1", reason: .missingSynchronization)])
        XCTAssertTrue(result.eligibleAssessmentIds.isEmpty, "today, NO assessment in this codebase can satisfy calibration-quality sync — see the phase report")
    }

    // MARK: Configuration (19-21) — protocol-level errors surfaced through preflight's gate

    func testDuplicateFingerprintsRejectedAtPreflightGate() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let invalidProtocol = TouchCandidateCalibrationProtocol(
            protocolId: "P1", datasetId: "D1", category: .exploratoryDevelopment, allowedSplits: [.development],
            minimumSynchronizationProvenance: .approximateHistoricalOffset, metricPolicy: .standard, matchingToleranceSeconds: 0.3,
            candidateConfigurationPolicy: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, .baseline]),
            leakagePolicy: .forbidCrossSplitGroupSharing
        )
        let result = TouchCandidateCalibrationPreflight.run(protocol: invalidProtocol, manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable)
        XCTAssertFalse(result.passed)
        XCTAssertEqual(result.gateFailures, [.invalidProtocol(.candidateConfigurationPolicyInvalid(.duplicateConfigurationFingerprint(TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)))])
    }

    func testInvalidDetectorConfigurationRejectedAtPreflightGate() throws {
        var invalid = TouchCandidateDetectorConfigurationSnapshot.baseline
        invalid.postWindowSeconds = -1
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let invalidProtocol = TouchCandidateCalibrationProtocol(
            protocolId: "P1", datasetId: "D1", category: .exploratoryDevelopment, allowedSplits: [.development],
            minimumSynchronizationProvenance: .approximateHistoricalOffset, metricPolicy: .standard, matchingToleranceSeconds: 0.3,
            candidateConfigurationPolicy: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, invalid]),
            leakagePolicy: .forbidCrossSplitGroupSharing
        )
        let result = TouchCandidateCalibrationPreflight.run(protocol: invalidProtocol, manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable)
        XCTAssertFalse(result.passed)
        XCTAssertEqual(result.gateFailures, [.invalidProtocol(.candidateConfigurationPolicyInvalid(.invalidCandidateConfiguration(.negativeDuration(field: "postWindowSeconds"))))])
    }

    func testBaselineMissingRejectedAtPreflightGate() throws {
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.03
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let invalidProtocol = TouchCandidateCalibrationProtocol(
            protocolId: "P1", datasetId: "D1", category: .exploratoryDevelopment, allowedSplits: [.development],
            minimumSynchronizationProvenance: .approximateHistoricalOffset, metricPolicy: .standard, matchingToleranceSeconds: 0.3,
            candidateConfigurationPolicy: TouchCandidateCandidateConfigurationSet(configurations: [alternate]),
            leakagePolicy: .forbidCrossSplitGroupSharing
        )
        let result = TouchCandidateCalibrationPreflight.run(protocol: invalidProtocol, manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable)
        XCTAssertFalse(result.passed)
        XCTAssertEqual(result.gateFailures, [.invalidProtocol(.candidateConfigurationPolicyInvalid(.baselineMissingFromCandidateSet))])
    }

    // MARK: Exclusions (22-26)

    func testMissingGroundTruthAccounted() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development],
            artifactAvailability: { _ in TouchCandidateArtifactAvailability(groundTruthAvailable: false, predictionsAvailable: true, observationCaptureAvailable: false) }
        )
        XCTAssertEqual(result.excluded, [TouchCandidatePreflightExclusion(assessmentId: "A1", reason: .missingGroundTruth)])
    }

    func testMissingObservationCaptureAndPredictionsAccountedAsMissingPredictions() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development],
            artifactAvailability: { _ in TouchCandidateArtifactAvailability(groundTruthAvailable: true, predictionsAvailable: false, observationCaptureAvailable: false) }
        )
        XCTAssertEqual(result.excluded, [TouchCandidatePreflightExclusion(assessmentId: "A1", reason: .missingPredictions)])
    }

    func testObservationCaptureAloneSatisfiesPredictionAvailability() throws {
        // A raw capture can be REPLAYED into predictions (TouchCandidateReplayEngine)
        // — it must count as "predictions obtainable," not excluded.
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(), manifest: manifest, requestedSplits: [.development],
            artifactAvailability: { _ in TouchCandidateArtifactAvailability(groundTruthAvailable: true, predictionsAvailable: false, observationCaptureAvailable: true) }
        )
        XCTAssertEqual(result.eligibleAssessmentIds, ["A1"])
    }

    func testInsufficientSynchronizationAccounted() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development, sync: .unavailable)])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(category: .exploratoryDevelopment), manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable
        )
        XCTAssertEqual(result.excluded, [TouchCandidatePreflightExclusion(assessmentId: "A1", reason: .missingSynchronization)])
    }

    func testExclusionCountsDeterministic() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, sync: .unavailable),
            entry("A2", split: .development, inclusion: .excluded(reason: .manualExclusion)),
            entry("A3", split: .development),
        ])
        let result = TouchCandidateCalibrationPreflight.run(
            protocol: protocolValue(category: .exploratoryDevelopment), manifest: manifest, requestedSplits: [.development],
            artifactAvailability: { entry in entry.assessmentId == "A3" ? TouchCandidateArtifactAvailability(groundTruthAvailable: true, predictionsAvailable: true, observationCaptureAvailable: false) : TouchCandidateArtifactAvailability(groundTruthAvailable: false, predictionsAvailable: false, observationCaptureAvailable: false) }
        )
        XCTAssertEqual(result.totalManifestEntries, 3)
        XCTAssertEqual(result.eligibleAssessmentIds, ["A3"])
        XCTAssertEqual(result.exclusionCountsByReason, [.missingSynchronization: 1, .manualExclusion: 1])
    }

    // MARK: Reproducibility (27-28) — preflight-specific

    func testSameDatasetAndProtocolProduceSamePreflightResult() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let proto = protocolValue()
        let result1 = TouchCandidateCalibrationPreflight.run(protocol: proto, manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable)
        let result2 = TouchCandidateCalibrationPreflight.run(protocol: proto, manifest: manifest, requestedSplits: [.development], artifactAvailability: fullyAvailable)
        XCTAssertEqual(result1, result2)
    }

    func testManifestEntryOrderingDoesNotChangeDatasetContentFingerprint() throws {
        let manifestA = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development), entry("A2", split: .calibration)])
        let manifestB = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A2", split: .calibration), entry("A1", split: .development)])
        XCTAssertEqual(manifestA.contentFingerprint, manifestB.contentFingerprint)
    }

    func testMaterialManifestContentChangeDoesChangeFingerprint() throws {
        let manifestA = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .development)])
        let manifestB = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", split: .calibration)])
        XCTAssertNotEqual(manifestA.contentFingerprint, manifestB.contentFingerprint)
    }

    // MARK: Holdout gate error paths (Part 9 firewall)

    func testHoldoutGateRejectsSealedProtocol() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("H1", split: .holdout, sync: .measuredFromCapturePTS)])
        let sealedProtocol = protocolValue(category: .finalHoldoutEvaluation, allowedSplits: [.holdout], holdoutPolicy: .sealed)
        XCTAssertThrowsError(try TouchCandidateHoldoutGate.runFinalHoldoutEvaluation(protocol: sealedProtocol, manifest: manifest, frozenConfiguration: .baseline, artifactAvailability: fullyAvailable)) { error in
            XCTAssertEqual(error as? TouchCandidateHoldoutGateError, .protocolNotUnsealedForHoldout)
        }
    }

    func testHoldoutGateRejectsWrongCategory() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        let wrongCategory = protocolValue(
            category: .calibrationQuality, allowedSplits: [.calibration],
            holdoutPolicy: .unsealedForFinalEvaluation(frozenConfigurationFingerprint: TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)
        )
        XCTAssertThrowsError(try TouchCandidateHoldoutGate.runFinalHoldoutEvaluation(protocol: wrongCategory, manifest: manifest, frozenConfiguration: .baseline, artifactAvailability: fullyAvailable)) { error in
            XCTAssertEqual(error as? TouchCandidateHoldoutGateError, .protocolCategoryNotFinalHoldoutEvaluation)
        }
    }

    func testHoldoutGateRejectsMismatchedFrozenConfiguration() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        var differentConfig = TouchCandidateDetectorConfigurationSnapshot.baseline
        differentConfig.minimumSpeedChangeForTouchCandidate = 0.03
        let unsealedForBaseline = protocolValue(
            category: .finalHoldoutEvaluation, allowedSplits: [.holdout],
            holdoutPolicy: .unsealedForFinalEvaluation(frozenConfigurationFingerprint: TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)
        )
        XCTAssertThrowsError(try TouchCandidateHoldoutGate.runFinalHoldoutEvaluation(protocol: unsealedForBaseline, manifest: manifest, frozenConfiguration: differentConfig, artifactAvailability: fullyAvailable)) { error in
            XCTAssertEqual(error as? TouchCandidateHoldoutGateError, .configurationFingerprintDoesNotMatchFrozenFingerprint)
        }
    }
}
