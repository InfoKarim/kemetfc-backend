//
//  TouchCandidateCalibrationPreflight.swift
//  KemetFCTrackerTests
//
//  Pre-flight validation for a `TouchCandidateCalibrationProtocol` (Parts 9,
//  12, 13, 2026-09-28 calibration experiment protocol phase). Two
//  STRUCTURALLY SEPARATE entry points exist:
//
//  1. `TouchCandidateCalibrationPreflight.run` — development/calibration
//     only. Its `requestedSplits` parameter is `[TouchCandidateNonHoldoutSplit]`,
//     a type that cannot represent `.holdout` at all, so a holdout request
//     cannot even be written down here, let alone accidentally executed.
//
//  2. `TouchCandidateHoldoutGate.runFinalHoldoutEvaluation` — the ONLY
//     function that may inspect `.holdout` entries. It requires the
//     protocol to be explicitly `.unsealedForFinalEvaluation` with a frozen
//     configuration fingerprint, and accepts exactly ONE detector
//     configuration (never a list) — ranking/selecting among configurations
//     against holdout data is impossible through this API by construction.
//
//  Neither function loads real files — `artifactAvailability` is injected,
//  exactly like `TouchCandidateExperimentRunner`'s own `loadGroundTruth`/
//  `loadPredictions` closures, keeping this fully testable with in-memory
//  fakes. This file performs NO threshold search, NO calibration, and NO
//  configuration comparison of any kind (Part 15).
//

import Foundation
@testable import KemetFCTracker

// MARK: - Artifact availability (Part 12/13)

/// What exists for one assessment, as far as this preflight is concerned —
/// never resolves paths or reads files itself (kept decoupled, same
/// principle as `TouchCandidateExperimentRunner`'s injected loaders).
/// `observationCaptureAvailable` lets a raw capture stand in for
/// `predictionsAvailable` (Part 12: "observation capture available when
/// replay is required") since `TouchCandidateReplayEngine` can produce a
/// `PredictionDataset` from a capture — either source counts as predictions
/// being obtainable.
public struct TouchCandidateArtifactAvailability: Equatable, Sendable {
    public var groundTruthAvailable: Bool
    public var predictionsAvailable: Bool
    public var observationCaptureAvailable: Bool

    public init(groundTruthAvailable: Bool, predictionsAvailable: Bool, observationCaptureAvailable: Bool) {
        self.groundTruthAvailable = groundTruthAvailable
        self.predictionsAvailable = predictionsAvailable
        self.observationCaptureAvailable = observationCaptureAvailable
    }

    var predictionsEffectivelyAvailable: Bool { predictionsAvailable || observationCaptureAvailable }
}

// MARK: - Preflight result (Part 13: exclusion accounting)

public struct TouchCandidatePreflightExclusion: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var reason: AssessmentExclusionReason
}

/// A protocol/global-level failure — reported BEFORE any per-assessment
/// scan happens (mirrors `TouchCandidateExperimentConfiguration
/// .validateSplitSafety()`'s "check first, touch nothing on failure"
/// pattern). Any one of these means `eligibleAssessmentIds` and `excluded`
/// are both empty — the preflight never partially scans after a gate fails.
public enum TouchCandidatePreflightGateFailure: Equatable, Sendable {
    case invalidProtocol(TouchCandidateCalibrationProtocolError)
    case requestedSplitNotPermittedByProtocol(TouchCandidateNonHoldoutSplit)
    case crossSplitGroupLeakageDetected([String])
    case emptyManifest
}

/// N total manifest entries, N eligible, N excluded with reason counts —
/// Part 13's exact requirement. Reproducible: the SAME protocol + manifest +
/// availability function always yields the SAME result (Part 16, item 27).
public struct TouchCandidatePreflightResult: Equatable, Sendable {
    public var protocolFingerprint: String
    public var datasetContentFingerprint: String
    public var totalManifestEntries: Int
    public var eligibleAssessmentIds: [String]
    public var excluded: [TouchCandidatePreflightExclusion]
    public var gateFailures: [TouchCandidatePreflightGateFailure]

    /// `true` exactly when no protocol/global-level gate failed — an
    /// assessment-level exclusion (e.g. one missing ground truth) does NOT
    /// make `passed` false; it is normal, accounted-for exclusion, not a
    /// gate failure.
    public var passed: Bool { gateFailures.isEmpty }

    /// Reason → count, for a final report's "N excluded, by reason" table
    /// (Part 13).
    public var exclusionCountsByReason: [AssessmentExclusionReason: Int] {
        Dictionary(grouping: excluded, by: { $0.reason }).mapValues { $0.count }
    }
}

// MARK: - Ordinary preflight (development/calibration ONLY)

public enum TouchCandidateCalibrationPreflight {
    /// `requestedSplits` is `[TouchCandidateNonHoldoutSplit]` — holdout is
    /// not a value this parameter can hold (Part 9's compile-time firewall).
    public static func run(
        protocol calibrationProtocol: TouchCandidateCalibrationProtocol,
        manifest: TouchCandidateDatasetManifest,
        requestedSplits: [TouchCandidateNonHoldoutSplit],
        artifactAvailability: (TouchCandidateDatasetAssessmentEntry) -> TouchCandidateArtifactAvailability
    ) -> TouchCandidatePreflightResult {
        let datasetFingerprint = manifest.contentFingerprint

        func failed(_ failures: [TouchCandidatePreflightGateFailure]) -> TouchCandidatePreflightResult {
            TouchCandidatePreflightResult(
                protocolFingerprint: calibrationProtocol.fingerprint, datasetContentFingerprint: datasetFingerprint,
                totalManifestEntries: manifest.assessments.count, eligibleAssessmentIds: [], excluded: [], gateFailures: failures
            )
        }

        do {
            try calibrationProtocol.validate()
        } catch let protocolError as TouchCandidateCalibrationProtocolError {
            return failed([.invalidProtocol(protocolError)])
        } catch {
            return failed([.invalidProtocol(.unsupportedSchemaVersion(calibrationProtocol.schemaVersion))])
        }

        for requested in requestedSplits {
            guard calibrationProtocol.allowedSplits.contains(requested.asDatasetSplit) else {
                return failed([.requestedSplitNotPermittedByProtocol(requested)])
            }
        }

        if calibrationProtocol.leakagePolicy == .forbidCrossSplitGroupSharing {
            let leakedGroups = manifest.crossSplitGroupLeakage()
            if !leakedGroups.isEmpty {
                return failed([.crossSplitGroupLeakageDetected(leakedGroups)])
            }
        }

        guard !manifest.assessments.isEmpty else {
            return failed([.emptyManifest])
        }

        let requestedDatasetSplits = Set(requestedSplits.map { $0.asDatasetSplit })
        var eligible: [String] = []
        var excluded: [TouchCandidatePreflightExclusion] = []

        for entry in manifest.assessments {
            guard requestedDatasetSplits.contains(entry.split) else { continue } // out of scope for this run entirely — not an exclusion

            if case .excluded(let reason) = entry.inclusionStatus {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: reason))
                continue
            }
            guard entry.synchronizationStatus.eligibilityRank >= calibrationProtocol.minimumSynchronizationProvenance.eligibilityRank else {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: .missingSynchronization))
                continue
            }
            let availability = artifactAvailability(entry)
            guard availability.groundTruthAvailable else {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: .missingGroundTruth))
                continue
            }
            guard availability.predictionsEffectivelyAvailable else {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: .missingPredictions))
                continue
            }
            eligible.append(entry.assessmentId)
        }

        return TouchCandidatePreflightResult(
            protocolFingerprint: calibrationProtocol.fingerprint, datasetContentFingerprint: datasetFingerprint,
            totalManifestEntries: manifest.assessments.count, eligibleAssessmentIds: eligible, excluded: excluded, gateFailures: []
        )
    }
}

// MARK: - Dedicated holdout gate (Part 9 — the ONLY holdout entry point)

public enum TouchCandidateHoldoutGateError: Error, Equatable {
    case invalidProtocol(TouchCandidateCalibrationProtocolError)
    case protocolCategoryNotFinalHoldoutEvaluation
    case protocolNotUnsealedForHoldout
    case configurationFingerprintDoesNotMatchFrozenFingerprint
}

public enum TouchCandidateHoldoutGate {
    /// Requires: (1) a VALID protocol, (2) `category == .finalHoldoutEvaluation`,
    /// (3) `holdoutPolicy == .unsealedForFinalEvaluation` with a frozen
    /// fingerprint, and (4) `frozenConfiguration.fingerprint` matching that
    /// EXACT frozen fingerprint — four independent conditions, all of which
    /// must be true before a single `.holdout` entry is ever read. There is
    /// no list-of-configurations parameter here; only one configuration can
    /// ever be evaluated per call.
    public static func runFinalHoldoutEvaluation(
        protocol calibrationProtocol: TouchCandidateCalibrationProtocol,
        manifest: TouchCandidateDatasetManifest,
        frozenConfiguration: TouchCandidateDetectorConfigurationSnapshot,
        artifactAvailability: (TouchCandidateDatasetAssessmentEntry) -> TouchCandidateArtifactAvailability
    ) throws -> TouchCandidatePreflightResult {
        do {
            try calibrationProtocol.validate()
        } catch let protocolError as TouchCandidateCalibrationProtocolError {
            throw TouchCandidateHoldoutGateError.invalidProtocol(protocolError)
        }
        guard calibrationProtocol.category == .finalHoldoutEvaluation else {
            throw TouchCandidateHoldoutGateError.protocolCategoryNotFinalHoldoutEvaluation
        }
        guard case .unsealedForFinalEvaluation(let frozenFingerprint) = calibrationProtocol.holdoutPolicy else {
            throw TouchCandidateHoldoutGateError.protocolNotUnsealedForHoldout
        }
        guard frozenFingerprint == frozenConfiguration.fingerprint else {
            throw TouchCandidateHoldoutGateError.configurationFingerprintDoesNotMatchFrozenFingerprint
        }

        var eligible: [String] = []
        var excluded: [TouchCandidatePreflightExclusion] = []
        for entry in manifest.assessments {
            guard entry.split == .holdout else { continue }
            if case .excluded(let reason) = entry.inclusionStatus {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: reason))
                continue
            }
            guard entry.synchronizationStatus.eligibilityRank >= calibrationProtocol.minimumSynchronizationProvenance.eligibilityRank else {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: .missingSynchronization))
                continue
            }
            let availability = artifactAvailability(entry)
            guard availability.groundTruthAvailable else {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: .missingGroundTruth))
                continue
            }
            guard availability.predictionsEffectivelyAvailable else {
                excluded.append(TouchCandidatePreflightExclusion(assessmentId: entry.assessmentId, reason: .missingPredictions))
                continue
            }
            eligible.append(entry.assessmentId)
        }

        return TouchCandidatePreflightResult(
            protocolFingerprint: calibrationProtocol.fingerprint, datasetContentFingerprint: manifest.contentFingerprint,
            totalManifestEntries: manifest.assessments.count, eligibleAssessmentIds: eligible, excluded: excluded, gateFailures: []
        )
    }
}
