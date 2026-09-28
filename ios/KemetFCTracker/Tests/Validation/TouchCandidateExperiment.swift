//
//  TouchCandidateExperiment.swift
//  KemetFCTrackerTests
//
//  Experiment configuration, detector-configuration reproducibility
//  snapshot + fingerprint, and the experiment runner (Parts 8-13,
//  2026-09-28 dataset & calibration foundation). Reuses the EXISTING
//  `TouchCandidateEvaluator`/`TouchCandidateAggregateValidationResult` for
//  all metric computation — this file never re-implements matching or
//  aggregation logic.
//

import Foundation
@testable import KemetFCTracker

// MARK: - Split safety (Part 5/15)

/// DEVELOPMENT: no leakage restriction. CALIBRATION: threshold/configuration
/// SELECTION — holdout is structurally rejected, never merely discouraged.
/// HOLDOUT: the final, single-shot, unbiased evaluation — only the holdout
/// split may participate.
public enum TouchCandidateExperimentPurpose: String, Codable, Equatable, Sendable {
    case developmentEvaluation
    case calibration
    case holdoutEvaluation
}

public enum TouchCandidateExperimentError: Error, Equatable {
    case holdoutNotAllowedInCalibration
    case nonHoldoutSplitInHoldoutEvaluation
}

/// Evaluation-side configuration ONLY (matching tolerance, which splits are
/// in scope, what synchronization quality is required). Deliberately
/// separate from `TouchCandidateDetectorConfigurationSnapshot` below —
/// Part 8: "matching tolerance is part of EVALUATION configuration.
/// Detector thresholds are part of DETECTOR configuration. Do not mix them."
public struct TouchCandidateExperimentConfiguration: Codable, Equatable, Sendable {
    public var experimentId: String
    public var datasetId: String
    public var purpose: TouchCandidateExperimentPurpose
    public var allowedSplits: [DatasetSplit]
    /// The MINIMUM synchronization provenance quality an assessment must
    /// have to participate. Never silently allows an approximate mapping
    /// into a strict experiment — the caller decides explicitly.
    public var requiredSynchronizationQuality: SynchronizationProvenance
    public var matchingToleranceSeconds: Double
    public var detectorConfigurationId: String?

    public init(
        experimentId: String, datasetId: String, purpose: TouchCandidateExperimentPurpose,
        allowedSplits: [DatasetSplit], requiredSynchronizationQuality: SynchronizationProvenance,
        matchingToleranceSeconds: Double, detectorConfigurationId: String? = nil
    ) {
        self.experimentId = experimentId
        self.datasetId = datasetId
        self.purpose = purpose
        self.allowedSplits = allowedSplits
        self.requiredSynchronizationQuality = requiredSynchronizationQuality
        self.matchingToleranceSeconds = matchingToleranceSeconds
        self.detectorConfigurationId = detectorConfigurationId
    }

    /// Called FIRST by the runner, before touching any data (Part 15's
    /// guardrail: "if a comparison/selection operation receives holdout
    /// entries in a calibration context, it must reject them").
    public func validateSplitSafety() throws {
        switch purpose {
        case .calibration:
            if allowedSplits.contains(.holdout) {
                throw TouchCandidateExperimentError.holdoutNotAllowedInCalibration
            }
        case .holdoutEvaluation:
            if allowedSplits.contains(where: { $0 != .holdout }) {
                throw TouchCandidateExperimentError.nonHoldoutSplitInHoldoutEvaluation
            }
        case .developmentEvaluation:
            break
        }
    }
}

extension SynchronizationProvenance {
    /// Ordering for ELIGIBILITY COMPARISONS ONLY — higher means more
    /// trustworthy. This does NOT introduce a "validated" tier; no such
    /// case exists on this enum (by design — see the real-data capture
    /// bridge audit: this project has only ever produced `.unavailable`/
    /// `.approximateHistoricalOffset` in practice).
    var eligibilityRank: Int {
        switch self {
        case .unavailable: return 0
        case .approximateHistoricalOffset: return 1
        case .derivedFromRecordedAsset: return 2
        case .measuredFromCapturePTS: return 3
        }
    }
}

// MARK: - Detector configuration snapshot + fingerprint (Parts 9-10)
//
// `TouchCandidateDetectorConfigurationSnapshot` itself now lives in
// Sources/Tracking/TouchCandidateDetectorConfigurationSnapshot.swift (moved
// there 2026-09-28, raw observation replay foundation — production capture
// code needs it directly to record configuration provenance at capture
// time). Visible here unchanged via `@testable import` above; no duplicate
// definition is kept.

// MARK: - Experiment result (Part 11)

public struct ExcludedAssessment: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var reason: AssessmentExclusionReason
}

/// Reuses `TouchCandidateAggregateValidationResult` directly for ALL metric
/// computation (micro TP/FP/FN/precision/recall/F1, aggregate timing,
/// per-session results) — never re-implemented here.
public struct TouchCandidateExperimentResult: Codable, Equatable, Sendable {
    public var experimentId: String
    public var datasetId: String
    public var detectorConfigurationFingerprint: String
    public var detectorConfigurationVersion: String
    public var evaluationConfiguration: TouchCandidateValidationConfiguration
    public var splitsEvaluated: [DatasetSplit]
    public var excludedAssessments: [ExcludedAssessment]
    public var aggregate: TouchCandidateAggregateValidationResult
    public var generatedAt: Date

    public init(
        experimentId: String, datasetId: String, detectorConfigurationFingerprint: String,
        detectorConfigurationVersion: String, evaluationConfiguration: TouchCandidateValidationConfiguration,
        splitsEvaluated: [DatasetSplit], excludedAssessments: [ExcludedAssessment],
        aggregate: TouchCandidateAggregateValidationResult, generatedAt: Date = Date()
    ) {
        self.experimentId = experimentId
        self.datasetId = datasetId
        self.detectorConfigurationFingerprint = detectorConfigurationFingerprint
        self.detectorConfigurationVersion = detectorConfigurationVersion
        self.evaluationConfiguration = evaluationConfiguration
        self.splitsEvaluated = splitsEvaluated
        self.excludedAssessments = excludedAssessments
        self.aggregate = aggregate
        self.generatedAt = generatedAt
    }
}

// MARK: - Runner

public enum TouchCandidateExperimentRunner {
    /// `loadGroundTruth`/`loadPredictions` are injected (not a hard-coded
    /// file layout) — keeps this runner fully testable with in-memory
    /// fakes and decoupled from any particular storage scheme. Returning
    /// nil means "not available for this assessment" (never thrown for
    /// that case) and becomes an explicit exclusion reason, never a crash.
    public static func run(
        experimentId: String, manifest: TouchCandidateDatasetManifest, configuration: TouchCandidateExperimentConfiguration,
        detectorConfiguration: TouchCandidateDetectorConfigurationSnapshot,
        loadGroundTruth: (TouchCandidateDatasetAssessmentEntry) -> GroundTruthDataset?,
        loadPredictions: (TouchCandidateDatasetAssessmentEntry) -> PredictionDataset?,
        loadMapping: (TouchCandidateDatasetAssessmentEntry) -> TimestampMapping
    ) throws -> TouchCandidateExperimentResult {
        try configuration.validateSplitSafety()

        let evalConfig = TouchCandidateValidationConfiguration(matchingToleranceSeconds: configuration.matchingToleranceSeconds)
        var excluded: [ExcludedAssessment] = []
        var perSessionResults: [TouchCandidateValidationResult] = []

        for entry in manifest.assessments {
            guard configuration.allowedSplits.contains(entry.split) else { continue } // out of scope for this experiment entirely

            if case .excluded(let reason) = entry.inclusionStatus {
                excluded.append(ExcludedAssessment(assessmentId: entry.assessmentId, reason: reason))
                continue
            }
            guard entry.synchronizationStatus.eligibilityRank >= configuration.requiredSynchronizationQuality.eligibilityRank else {
                excluded.append(ExcludedAssessment(assessmentId: entry.assessmentId, reason: .missingSynchronization))
                continue
            }
            guard let groundTruth = loadGroundTruth(entry) else {
                excluded.append(ExcludedAssessment(assessmentId: entry.assessmentId, reason: .missingGroundTruth))
                continue
            }
            guard let predictions = loadPredictions(entry) else {
                excluded.append(ExcludedAssessment(assessmentId: entry.assessmentId, reason: .missingPredictions))
                continue
            }
            do {
                let result = try TouchCandidateEvaluator.evaluate(
                    groundTruth: groundTruth, predictions: predictions,
                    mapping: loadMapping(entry), configuration: evalConfig
                )
                perSessionResults.append(result)
            } catch {
                excluded.append(ExcludedAssessment(assessmentId: entry.assessmentId, reason: .malformedArtifact))
            }
        }

        return TouchCandidateExperimentResult(
            experimentId: experimentId, datasetId: manifest.datasetId,
            detectorConfigurationFingerprint: detectorConfiguration.fingerprint,
            detectorConfigurationVersion: detectorConfiguration.version,
            evaluationConfiguration: evalConfig, splitsEvaluated: configuration.allowedSplits,
            excludedAssessments: excluded, aggregate: TouchCandidateEvaluator.aggregate(perSessionResults)
        )
    }
}
