//
//  TouchCandidateCalibrationProtocolTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 16's PROTOCOL (1-7) and REPRODUCIBILITY (27-30) items.
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateCalibrationProtocolTests: XCTestCase {
    private func candidateSet(includeAlternate: Bool = false) -> TouchCandidateCandidateConfigurationSet {
        var configs = [TouchCandidateDetectorConfigurationSnapshot.baseline]
        if includeAlternate {
            var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
            alternate.minimumSpeedChangeForTouchCandidate = 0.03
            configs.append(alternate)
        }
        return TouchCandidateCandidateConfigurationSet(configurations: configs)
    }

    private func protocolValue(
        category: TouchCandidateExperimentCategory = .exploratoryDevelopment,
        allowedSplits: [DatasetSplit] = [.development],
        holdoutPolicy: TouchCandidateHoldoutPolicy = .sealed,
        leakagePolicy: TouchCandidateLeakagePolicy = .forbidCrossSplitGroupSharing,
        candidates: TouchCandidateCandidateConfigurationSet? = nil,
        metricPolicy: TouchCandidateCalibrationMetricPolicy = .standard,
        matchingToleranceSeconds: Double = 0.3
    ) -> TouchCandidateCalibrationProtocol {
        TouchCandidateCalibrationProtocol(
            protocolId: "PROTO-1", datasetId: "D1", category: category, allowedSplits: allowedSplits,
            minimumSynchronizationProvenance: category.minimumSynchronizationProvenance, metricPolicy: metricPolicy,
            matchingToleranceSeconds: matchingToleranceSeconds, candidateConfigurationPolicy: candidates ?? candidateSet(),
            holdoutPolicy: holdoutPolicy, leakagePolicy: leakagePolicy
        )
    }

    // MARK: Protocol (1-7)

    func testProtocolCodableRoundTrip() throws {
        // Whole-second createdAt (matches the established convention in
        // TouchCandidateObservationCaptureTests) — ISO 8601 without
        // fractional seconds would otherwise truncate `Date()`'s
        // sub-second precision and make a bit-exact round-trip fail on an
        // unrelated technicality.
        var original = protocolValue(candidates: candidateSet(includeAlternate: true))
        original.createdAt = Date(timeIntervalSince1970: 1_700_000_000)
        let data = try TouchCandidateCalibrationProtocolIO.encoder().encode(original)
        let decoded = try TouchCandidateCalibrationProtocolIO.decoder().decode(TouchCandidateCalibrationProtocol.self, from: data)
        XCTAssertEqual(decoded, original)
        XCTAssertEqual(decoded.fingerprint, original.fingerprint)
    }

    func testHoldoutPolicyUnsealedRoundTrips() throws {
        let original = protocolValue(
            category: .finalHoldoutEvaluation, allowedSplits: [.holdout],
            holdoutPolicy: .unsealedForFinalEvaluation(frozenConfigurationFingerprint: "abc123")
        )
        let data = try TouchCandidateCalibrationProtocolIO.encoder().encode(original)
        let decoded = try TouchCandidateCalibrationProtocolIO.decoder().decode(TouchCandidateCalibrationProtocol.self, from: data)
        XCTAssertEqual(decoded.holdoutPolicy, .unsealedForFinalEvaluation(frozenConfigurationFingerprint: "abc123"))
    }

    func testDeterministicFingerprint() {
        let a = protocolValue()
        let b = protocolValue()
        XCTAssertEqual(a.fingerprint, b.fingerprint)
    }

    func testFieldChangeChangesFingerprint() {
        let a = protocolValue()
        let b = protocolValue(matchingToleranceSeconds: 0.31)
        XCTAssertNotEqual(a.fingerprint, b.fingerprint)
    }

    func testUnsupportedSchemaVersionRejected() {
        var p = protocolValue()
        p.schemaVersion = 999
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .unsupportedSchemaVersion(999))
        }
    }

    func testMatchingToleranceIncludedInFingerprint() {
        let a = protocolValue(matchingToleranceSeconds: 0.2)
        let b = protocolValue(matchingToleranceSeconds: 0.5)
        XCTAssertNotEqual(a.fingerprint, b.fingerprint)
    }

    func testCandidateConfigFingerprintsIncludedInProtocolFingerprint() {
        let a = protocolValue(candidates: candidateSet(includeAlternate: false))
        let b = protocolValue(candidates: candidateSet(includeAlternate: true))
        XCTAssertNotEqual(a.fingerprint, b.fingerprint)
    }

    func testBaselineRequirementEnforced() {
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.03
        let p = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [alternate]))
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .candidateConfigurationPolicyInvalid(.baselineMissingFromCandidateSet))
        }
    }

    // MARK: Additional protocol-level guardrails

    func testEmptyObjectiveMetricsRejected() {
        let p = protocolValue(metricPolicy: TouchCandidateCalibrationMetricPolicy(objectiveMetrics: [], diagnosticMetrics: [.precision]))
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .emptyObjectiveMetrics)
        }
    }

    func testOverlappingObjectiveAndDiagnosticMetricsRejected() {
        let p = protocolValue(metricPolicy: TouchCandidateCalibrationMetricPolicy(objectiveMetrics: [.precision], diagnosticMetrics: [.precision]))
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .objectiveAndDiagnosticMetricsOverlap)
        }
    }

    func testHoldoutSplitOutsideFinalEvaluationCategoryRejected() {
        let p = protocolValue(category: .calibrationQuality, allowedSplits: [.calibration, .holdout])
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .holdoutSplitOutsideFinalEvaluationCategory)
        }
    }

    func testLeakagePolicyNoneOutsideSmokeTestCategoryRejected() {
        let p = protocolValue(category: .exploratoryDevelopment, leakagePolicy: .none)
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .leakagePolicyNoneOutsideSmokeTestCategory)
        }
    }

    func testNonFiniteOrNonPositiveMatchingToleranceRejected() {
        for bad in [0.0, -0.1, Double.nan, Double.infinity] {
            let p = protocolValue(matchingToleranceSeconds: bad)
            XCTAssertThrowsError(try p.validate(), "tolerance \(bad) must be rejected") { error in
                XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .nonPositiveMatchingTolerance)
            }
        }
    }

    func testDuplicateCandidateFingerprintRejected() {
        let p = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, .baseline]))
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .candidateConfigurationPolicyInvalid(.duplicateConfigurationFingerprint(TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint)))
        }
    }

    func testInvalidCandidateConfigurationRejected() {
        var invalid = TouchCandidateDetectorConfigurationSnapshot.baseline
        invalid.cooldownDurationSeconds = -1
        let p = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, invalid]))
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .candidateConfigurationPolicyInvalid(.invalidCandidateConfiguration(.negativeDuration(field: "cooldownDurationSeconds"))))
        }
    }

    func testEmptyCandidateSetRejected() {
        let p = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: []))
        XCTAssertThrowsError(try p.validate()) { error in
            XCTAssertEqual(error as? TouchCandidateCalibrationProtocolError, .candidateConfigurationPolicyInvalid(.emptyCandidateSet))
        }
    }

    // MARK: Reproducibility (27-30) — protocol-identity portion

    func testSameProtocolContentSameFingerprintAcrossIndependentConstructions() {
        let a = protocolValue(candidates: candidateSet(includeAlternate: true))
        let b = protocolValue(candidates: candidateSet(includeAlternate: true))
        XCTAssertEqual(a.fingerprint, b.fingerprint, "two independently-constructed but content-identical protocols must fingerprint identically")
    }

    func testAllowedSplitsOrderingDoesNotAffectFingerprint() {
        let a = protocolValue(allowedSplits: [.development, .calibration])
        let b = protocolValue(allowedSplits: [.calibration, .development])
        XCTAssertEqual(a.fingerprint, b.fingerprint, "allowedSplits is semantically a SET — order must not matter")
    }

    func testCandidateConfigurationOrderingDoesNotAffectFingerprint() {
        var alternate = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate.minimumSpeedChangeForTouchCandidate = 0.03
        let a = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, alternate]))
        let b = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [alternate, .baseline]))
        XCTAssertEqual(a.fingerprint, b.fingerprint, "candidate set ordering must not matter — it is a SET, not a sequence")
    }

    func testMaterialCandidateConfigurationChangeDoesChangeFingerprint() {
        var alternate1 = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate1.minimumSpeedChangeForTouchCandidate = 0.03
        var alternate2 = TouchCandidateDetectorConfigurationSnapshot.baseline
        alternate2.minimumSpeedChangeForTouchCandidate = 0.05
        let a = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, alternate1]))
        let b = protocolValue(candidates: TouchCandidateCandidateConfigurationSet(configurations: [.baseline, alternate2]))
        XCTAssertNotEqual(a.fingerprint, b.fingerprint)
    }

    func testProtocolIdAndCreatedAtDoNotAffectFingerprint() {
        let a = TouchCandidateCalibrationProtocol(
            protocolId: "PROTO-A", createdAt: Date(timeIntervalSince1970: 1000),
            datasetId: "D1", category: .exploratoryDevelopment, allowedSplits: [.development],
            minimumSynchronizationProvenance: .approximateHistoricalOffset, metricPolicy: .standard,
            matchingToleranceSeconds: 0.3, candidateConfigurationPolicy: candidateSet(), leakagePolicy: .forbidCrossSplitGroupSharing
        )
        let b = TouchCandidateCalibrationProtocol(
            protocolId: "PROTO-B", createdAt: Date(timeIntervalSince1970: 999999),
            datasetId: "D1", category: .exploratoryDevelopment, allowedSplits: [.development],
            minimumSynchronizationProvenance: .approximateHistoricalOffset, metricPolicy: .standard,
            matchingToleranceSeconds: 0.3, candidateConfigurationPolicy: candidateSet(), leakagePolicy: .forbidCrossSplitGroupSharing
        )
        XCTAssertEqual(a.fingerprint, b.fingerprint, "protocolId/createdAt are labels, not content")
    }
}
