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

import CryptoKit
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

/// An IMMUTABLE record of the detector settings that produced a given set
/// of predictions — for REPRODUCIBILITY, not for injecting configuration
/// into the live detector (which does not accept injected configuration
/// today — see the Part 1 audit). Fields are exactly the 7 constants
/// confirmed to actually affect `TouchCandidateDetector`'s output.
public struct TouchCandidateDetectorConfigurationSnapshot: Codable, Equatable, Sendable {
    public var version: String
    public var preWindowSeconds: TimeInterval
    public var postWindowSeconds: TimeInterval
    public var cooldownDurationSeconds: TimeInterval
    public var dropoutGraceDurationSeconds: TimeInterval
    public var minimumSpeedChangeForTouchCandidate: Double
    public var maximumDirectionSimilarityForTouchCandidate: Double
    public var minimumSpeedForMeaningfulDirection: Double

    public init(
        version: String, preWindowSeconds: TimeInterval, postWindowSeconds: TimeInterval,
        cooldownDurationSeconds: TimeInterval, dropoutGraceDurationSeconds: TimeInterval,
        minimumSpeedChangeForTouchCandidate: Double, maximumDirectionSimilarityForTouchCandidate: Double,
        minimumSpeedForMeaningfulDirection: Double
    ) {
        self.version = version
        self.preWindowSeconds = preWindowSeconds
        self.postWindowSeconds = postWindowSeconds
        self.cooldownDurationSeconds = cooldownDurationSeconds
        self.dropoutGraceDurationSeconds = dropoutGraceDurationSeconds
        self.minimumSpeedChangeForTouchCandidate = minimumSpeedChangeForTouchCandidate
        self.maximumDirectionSimilarityForTouchCandidate = maximumDirectionSimilarityForTouchCandidate
        self.minimumSpeedForMeaningfulDirection = minimumSpeedForMeaningfulDirection
    }

    /// The CURRENT, live `TouchCandidateCalibration` constants, read
    /// directly — a reproducible starting point ONLY. Never labeled
    /// "optimized" or "calibrated" (Part 13), and this snapshot changes
    /// NOTHING about the live detector's actual behavior.
    public static let baseline = TouchCandidateDetectorConfigurationSnapshot(
        version: "baseline-\(TouchCandidateDetector.algorithmVersion)",
        preWindowSeconds: TouchCandidateCalibration.preWindowSeconds,
        postWindowSeconds: TouchCandidateCalibration.postWindowSeconds,
        cooldownDurationSeconds: TouchCandidateCalibration.cooldownDurationSeconds,
        dropoutGraceDurationSeconds: TouchCandidateCalibration.dropoutGraceDurationSeconds,
        minimumSpeedChangeForTouchCandidate: TouchCandidateCalibration.minimumSpeedChangeForTouchCandidate,
        maximumDirectionSimilarityForTouchCandidate: TouchCandidateCalibration.maximumDirectionSimilarityForTouchCandidate,
        minimumSpeedForMeaningfulDirection: TouchCandidateCalibration.minimumSpeedForMeaningfulDirection
    )

    /// Fixed field order and fixed decimal precision — independent of any
    /// dictionary/JSON key ordering, and human-readable on its own.
    public var canonicalDescription: String {
        func fmt(_ value: Double) -> String { String(format: "%.10f", value) }
        return "v=\(version)|pre=\(fmt(preWindowSeconds))|post=\(fmt(postWindowSeconds))"
            + "|cooldown=\(fmt(cooldownDurationSeconds))|grace=\(fmt(dropoutGraceDurationSeconds))"
            + "|minSpeedChange=\(fmt(minimumSpeedChangeForTouchCandidate))"
            + "|maxDirSim=\(fmt(maximumDirectionSimilarityForTouchCandidate))"
            + "|minSpeedDir=\(fmt(minimumSpeedForMeaningfulDirection))"
    }

    /// SHA-256 of `canonicalDescription`, hex-encoded. Uses CryptoKit (an
    /// Apple system framework — not a third-party dependency) rather than
    /// Swift's `Hashable`/`hashValue`, which is explicitly documented as
    /// randomized per PROCESS and therefore unsuitable for a "same
    /// configuration → same fingerprint" requirement that must be stable
    /// across separate runs.
    public var fingerprint: String {
        let digest = SHA256.hash(data: Data(canonicalDescription.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

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
