//
//  TouchCandidateCalibrationProtocol.swift
//  KemetFCTrackerTests
//
//  Experiment-GOVERNANCE infrastructure for future Touch Candidate detector
//  calibration (2026-09-28, calibration experiment protocol phase). This
//  file answers "what experiments are scientifically allowed, on which
//  data, with which metrics, under what rules" — it performs NO threshold
//  search, NO configuration ranking/selection, and NEVER inspects holdout
//  data. Reuses every existing type it can (`DatasetSplit`,
//  `SynchronizationProvenance`, `TouchCandidateDetectorConfigurationSnapshot`,
//  `TouchCandidateDatasetManifest`) rather than duplicating them.
//

import CryptoKit
import Foundation
@testable import KemetFCTracker

// MARK: - Metrics (Part 6) — named, not invented

/// Every metric `TouchCandidateEvaluator`/`TouchCandidateAggregateValidationResult`
/// already produces, given a stable identifier so a protocol can declare
/// which ones are objective vs diagnostic BEFORE any result exists. No new
/// metric is computed anywhere in this file.
public enum TouchCandidateCalibrationMetric: String, Codable, Equatable, Sendable, CaseIterable {
    case precision
    case recall
    case f1Score
    case truePositiveCount
    case falsePositiveCount
    case falseNegativeCount
    case meanSignedTimingErrorSeconds
    case meanAbsoluteTimingErrorSeconds
    case medianAbsoluteTimingErrorSeconds
    case maximumAbsoluteTimingErrorSeconds
}

/// PRIMARY/OBJECTIVE metrics are the only ones a FUTURE, separately-approved
/// calibration phase may use to compare configurations. DIAGNOSTIC metrics
/// are informative only and must never be used as a selection basis (Part
/// 6) — this phase enforces only that the two sets are declared and
/// disjoint; it makes no comparison or selection itself (Part 15).
public struct TouchCandidateCalibrationMetricPolicy: Codable, Equatable, Sendable {
    public var objectiveMetrics: [TouchCandidateCalibrationMetric]
    public var diagnosticMetrics: [TouchCandidateCalibrationMetric]

    public init(objectiveMetrics: [TouchCandidateCalibrationMetric], diagnosticMetrics: [TouchCandidateCalibrationMetric]) {
        self.objectiveMetrics = objectiveMetrics
        self.diagnosticMetrics = diagnosticMetrics
    }

    /// A reasonable, non-binding starting policy: F1/precision/recall as
    /// objective, raw counts and timing as diagnostic. Never itself used to
    /// select anything — a future calibration phase may declare a different
    /// policy entirely.
    public static let standard = TouchCandidateCalibrationMetricPolicy(
        objectiveMetrics: [.precision, .recall, .f1Score],
        diagnosticMetrics: [.truePositiveCount, .falsePositiveCount, .falseNegativeCount, .meanAbsoluteTimingErrorSeconds, .medianAbsoluteTimingErrorSeconds, .maximumAbsoluteTimingErrorSeconds, .meanSignedTimingErrorSeconds]
    )
}

// MARK: - Candidate configuration policy (Part 8)

public enum TouchCandidateCalibrationPolicyError: Error, Equatable {
    case emptyCandidateSet
    case baselineMissingFromCandidateSet
    case duplicateConfigurationFingerprint(String)
    case invalidCandidateConfiguration(TouchCandidateDetectorConfigurationSnapshotValidationError)
}

/// A PRE-DECLARED, FINITE list of detector configurations for a future
/// calibration phase to compare — never generated, mutated, or extended
/// after an experiment begins (Part 8: "future calibration must NOT
/// silently generate new candidates after seeing calibration results"; any
/// adaptive/generative search requires its own, separately-approved future
/// protocol). `.baseline` must always be present as the reference point.
public struct TouchCandidateCandidateConfigurationSet: Codable, Equatable, Sendable {
    public var configurations: [TouchCandidateDetectorConfigurationSnapshot]

    public init(configurations: [TouchCandidateDetectorConfigurationSnapshot]) {
        self.configurations = configurations
    }

    /// Validates presence/validity/uniqueness only — performs NO comparison,
    /// ranking, or evaluation of any configuration against any data.
    public func validate() throws {
        guard !configurations.isEmpty else {
            throw TouchCandidateCalibrationPolicyError.emptyCandidateSet
        }
        guard configurations.contains(where: { $0.fingerprint == TouchCandidateDetectorConfigurationSnapshot.baseline.fingerprint }) else {
            throw TouchCandidateCalibrationPolicyError.baselineMissingFromCandidateSet
        }
        var seenFingerprints = Set<String>()
        for configuration in configurations {
            do {
                try configuration.validate()
            } catch let validationError as TouchCandidateDetectorConfigurationSnapshotValidationError {
                throw TouchCandidateCalibrationPolicyError.invalidCandidateConfiguration(validationError)
            }
            guard seenFingerprints.insert(configuration.fingerprint).inserted else {
                throw TouchCandidateCalibrationPolicyError.duplicateConfigurationFingerprint(configuration.fingerprint)
            }
        }
    }

    /// Order-independent identity (Part 16, item 29: "configuration ordering
    /// does not change identity if treated as a set") — fingerprints are
    /// sorted before hashing, so the SAME set of configurations always
    /// yields the same value regardless of how it was constructed.
    public var setFingerprint: String {
        let sorted = configurations.map { $0.fingerprint }.sorted()
        let digest = SHA256.hash(data: Data(sorted.joined(separator: "|").utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - Leakage policy (Part 3)

/// Reuses `TouchCandidateDatasetManifest.crossSplitGroupLeakage()` directly
/// — never a duplicated leakage algorithm. `groupId` is optional and, per
/// the current audit, never actually populated anywhere in this codebase
/// today (no player/session-level grouping identifier exists yet) — see
/// the phase report for this explicit limitation.
public enum TouchCandidateLeakagePolicy: String, Codable, Equatable, Sendable {
    /// No `groupId` may appear in more than one split among this protocol's
    /// `allowedSplits`. The only policy permitted for any category above
    /// `.infrastructureSmokeTest`.
    case forbidCrossSplitGroupSharing
    /// No leakage protection is required — synthetic/plumbing smoke tests
    /// only, never a real dataset.
    case none
}

// MARK: - Holdout policy (Part 9)

/// Structurally excludes `.holdout` from the ordinary development/
/// calibration preflight's vocabulary entirely — a holdout request cannot
/// even be REPRESENTED by this type, which is the only split type the
/// ordinary preflight (`TouchCandidateCalibrationPreflight`) accepts. This
/// is a compile-time firewall, not a runtime check (Part 9: "design the API
/// so accidental holdout inspection is difficult").
public enum TouchCandidateNonHoldoutSplit: String, Codable, Equatable, Sendable, CaseIterable {
    case development
    case calibration

    public var asDatasetSplit: DatasetSplit {
        switch self {
        case .development: return .development
        case .calibration: return .calibration
        }
    }
}

/// A protocol starts `.sealed` and can never accidentally drift into
/// holdout visibility — the ONLY way to reach `.unsealedForFinalEvaluation`
/// is to construct a NEW protocol value with that case explicitly, and the
/// ONLY function that reads this case at all is
/// `TouchCandidateHoldoutGate.runFinalHoldoutEvaluation` (Part 9). The
/// ordinary preflight never inspects this field.
public enum TouchCandidateHoldoutPolicy: Codable, Equatable, Sendable {
    case sealed
    /// `frozenConfigurationFingerprint` restricts the eventual holdout
    /// evaluation to EXACTLY ONE configuration — this makes comparing or
    /// ranking multiple configurations against holdout data structurally
    /// impossible through this policy, not merely discouraged.
    case unsealedForFinalEvaluation(frozenConfigurationFingerprint: String)

    private enum CodingKeys: String, CodingKey { case kind, frozenConfigurationFingerprint }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let kind = try container.decode(String.self, forKey: .kind)
        switch kind {
        case "sealed": self = .sealed
        case "unsealedForFinalEvaluation":
            self = .unsealedForFinalEvaluation(frozenConfigurationFingerprint: try container.decode(String.self, forKey: .frozenConfigurationFingerprint))
        default:
            throw DecodingError.dataCorruptedError(forKey: .kind, in: container, debugDescription: "Unknown TouchCandidateHoldoutPolicy kind: \(kind)")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .sealed:
            try container.encode("sealed", forKey: .kind)
        case .unsealedForFinalEvaluation(let fingerprint):
            try container.encode("unsealedForFinalEvaluation", forKey: .kind)
            try container.encode(fingerprint, forKey: .frozenConfigurationFingerprint)
        }
    }

    /// Fixed-form description for the protocol's own canonical fingerprint —
    /// never affected by JSON key ordering.
    var canonicalDescription: String {
        switch self {
        case .sealed: return "sealed"
        case .unsealedForFinalEvaluation(let fingerprint): return "unsealed:\(fingerprint)"
        }
    }
}

// MARK: - Experiment category / synchronization eligibility (Part 4)

/// The four experiment CATEGORIES this protocol distinguishes, each with
/// its own MINIMUM required `SynchronizationProvenance` (see
/// `SynchronizationProvenance.eligibilityRank` in
/// `TouchCandidateExperiment.swift`). Per the current audit: this
/// codebase's data pipeline has never produced anything at or above
/// `.derivedFromRecordedAsset` (rank 2) — only `.unavailable` (0) and
/// `.approximateHistoricalOffset` (1) are ever actually generated anywhere
/// today. This means NO assessment can currently satisfy
/// `.calibrationQuality` or `.finalHoldoutEvaluation`'s minimum — a real,
/// current limitation, never weakened here to make experiments runnable.
public enum TouchCandidateExperimentCategory: String, Codable, Equatable, Sendable, CaseIterable {
    case infrastructureSmokeTest
    case exploratoryDevelopment
    case calibrationQuality
    case finalHoldoutEvaluation

    public var minimumSynchronizationProvenance: SynchronizationProvenance {
        switch self {
        case .infrastructureSmokeTest: return .unavailable
        case .exploratoryDevelopment: return .approximateHistoricalOffset
        case .calibrationQuality: return .derivedFromRecordedAsset
        case .finalHoldoutEvaluation: return .measuredFromCapturePTS
        }
    }
}

// MARK: - The protocol itself (Part 5)

public enum TouchCandidateCalibrationProtocolError: Error, Equatable {
    case unsupportedSchemaVersion(Int)
    case emptyObjectiveMetrics
    case objectiveAndDiagnosticMetricsOverlap
    case nonPositiveMatchingTolerance
    case candidateConfigurationPolicyInvalid(TouchCandidateCalibrationPolicyError)
    /// `.holdout` may appear in `allowedSplits` ONLY when `category ==
    /// .finalHoldoutEvaluation` — any other category listing it is rejected
    /// outright (a second, independent line of defense alongside
    /// `TouchCandidateNonHoldoutSplit`'s compile-time exclusion).
    case holdoutSplitOutsideFinalEvaluationCategory
    case leakagePolicyNoneOutsideSmokeTestCategory
}

/// A REPRODUCIBLE specification of one calibration experiment's rules —
/// content-addressed via `fingerprint`. Two protocol VALUES with identical
/// content (regardless of field/JSON ordering or `protocolId`/`createdAt`,
/// which are labels, not content) always fingerprint identically. This
/// phase performs no experiment run — the protocol only declares what a
/// FUTURE, separately-approved run would be allowed to do.
public struct TouchCandidateCalibrationProtocol: Codable, Equatable, Sendable {
    public static let currentSchemaVersion = 1
    public static let supportedSchemaVersions: Set<Int> = [1]

    public var schemaVersion: Int
    /// A label, not content — excluded from `fingerprint` (like
    /// `TouchCandidateExperimentConfiguration.experimentId`).
    public var protocolId: String
    public var createdAt: Date

    public var datasetId: String
    public var category: TouchCandidateExperimentCategory
    public var allowedSplits: [DatasetSplit]
    public var minimumSynchronizationProvenance: SynchronizationProvenance

    public var metricPolicy: TouchCandidateCalibrationMetricPolicy
    /// Evaluation METHODOLOGY, never detector configuration (Part 7) — kept
    /// entirely separate from `candidateConfigurationPolicy` below, exactly
    /// as `TouchCandidateValidationConfiguration` is already kept separate
    /// from `TouchCandidateDetectorConfigurationSnapshot` elsewhere in this
    /// codebase.
    public var matchingToleranceSeconds: Double

    public var candidateConfigurationPolicy: TouchCandidateCandidateConfigurationSet
    public var holdoutPolicy: TouchCandidateHoldoutPolicy
    public var leakagePolicy: TouchCandidateLeakagePolicy

    public init(
        schemaVersion: Int = TouchCandidateCalibrationProtocol.currentSchemaVersion, protocolId: String, createdAt: Date = Date(),
        datasetId: String, category: TouchCandidateExperimentCategory, allowedSplits: [DatasetSplit],
        minimumSynchronizationProvenance: SynchronizationProvenance, metricPolicy: TouchCandidateCalibrationMetricPolicy,
        matchingToleranceSeconds: Double, candidateConfigurationPolicy: TouchCandidateCandidateConfigurationSet,
        holdoutPolicy: TouchCandidateHoldoutPolicy = .sealed, leakagePolicy: TouchCandidateLeakagePolicy
    ) {
        self.schemaVersion = schemaVersion
        self.protocolId = protocolId
        self.createdAt = createdAt
        self.datasetId = datasetId
        self.category = category
        self.allowedSplits = allowedSplits
        self.minimumSynchronizationProvenance = minimumSynchronizationProvenance
        self.metricPolicy = metricPolicy
        self.matchingToleranceSeconds = matchingToleranceSeconds
        self.candidateConfigurationPolicy = candidateConfigurationPolicy
        self.holdoutPolicy = holdoutPolicy
        self.leakagePolicy = leakagePolicy
    }

    /// Called before ANY preflight or gate touches data — mirrors
    /// `TouchCandidateExperimentConfiguration.validateSplitSafety()`'s own
    /// "check first, touch nothing on failure" pattern.
    public func validate() throws {
        guard Self.supportedSchemaVersions.contains(schemaVersion) else {
            throw TouchCandidateCalibrationProtocolError.unsupportedSchemaVersion(schemaVersion)
        }
        guard matchingToleranceSeconds.isFinite, matchingToleranceSeconds > 0 else {
            throw TouchCandidateCalibrationProtocolError.nonPositiveMatchingTolerance
        }
        let objectiveSet = Set(metricPolicy.objectiveMetrics)
        guard !objectiveSet.isEmpty else {
            throw TouchCandidateCalibrationProtocolError.emptyObjectiveMetrics
        }
        guard objectiveSet.isDisjoint(with: Set(metricPolicy.diagnosticMetrics)) else {
            throw TouchCandidateCalibrationProtocolError.objectiveAndDiagnosticMetricsOverlap
        }
        if allowedSplits.contains(.holdout), category != .finalHoldoutEvaluation {
            throw TouchCandidateCalibrationProtocolError.holdoutSplitOutsideFinalEvaluationCategory
        }
        if leakagePolicy == .none, category != .infrastructureSmokeTest {
            throw TouchCandidateCalibrationProtocolError.leakagePolicyNoneOutsideSmokeTestCategory
        }
        do {
            try candidateConfigurationPolicy.validate()
        } catch let policyError as TouchCandidateCalibrationPolicyError {
            throw TouchCandidateCalibrationProtocolError.candidateConfigurationPolicyInvalid(policyError)
        }
    }

    /// Fixed field order, fixed decimal precision — same convention as
    /// `TouchCandidateDetectorConfigurationSnapshot.canonicalDescription`.
    /// Deliberately excludes `protocolId`/`createdAt` (labels, not content
    /// that affects interpretation) — see the type's own doc.
    public var canonicalDescription: String {
        let splits = allowedSplits.map { $0.rawValue }.sorted().joined(separator: ",")
        let objective = metricPolicy.objectiveMetrics.map { $0.rawValue }.sorted().joined(separator: ",")
        let diagnostic = metricPolicy.diagnosticMetrics.map { $0.rawValue }.sorted().joined(separator: ",")
        return "schema=\(schemaVersion)|dataset=\(datasetId)|category=\(category.rawValue)"
            + "|splits=\(splits)|minSync=\(minimumSynchronizationProvenance.rawValue)"
            + "|objective=\(objective)|diagnostic=\(diagnostic)"
            + "|tolerance=\(String(format: "%.10f", matchingToleranceSeconds))"
            + "|candidateSet=\(candidateConfigurationPolicy.setFingerprint)"
            + "|holdout=\(holdoutPolicy.canonicalDescription)|leakage=\(leakagePolicy.rawValue)"
    }

    /// SHA-256 of `canonicalDescription`, hex-encoded — same CryptoKit
    /// convention as `TouchCandidateDetectorConfigurationSnapshot.fingerprint`
    /// (never Swift's `Hashable`/`hashValue`, which is randomized per
    /// process).
    public var fingerprint: String {
        let digest = SHA256.hash(data: Data(canonicalDescription.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - Deterministic JSON (Part 10: "canonical serialization must be deterministic")

public enum TouchCandidateCalibrationProtocolIO {
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
