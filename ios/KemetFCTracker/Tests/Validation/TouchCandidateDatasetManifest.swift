//
//  TouchCandidateDatasetManifest.swift
//  KemetFCTrackerTests
//
//  Dataset + split + leakage-protection architecture for future Touch
//  Candidate calibration (2026-09-28, dataset & calibration foundation).
//  This phase builds MACHINERY only — no real calibration is performed, no
//  detector threshold is changed. Pure, offline, Tests/Validation/-only.
//

import CryptoKit
import Foundation
@testable import KemetFCTracker

// MARK: - Splits (Part 4)

/// DEVELOPMENT: used repeatedly while developing detector logic and
/// inspecting errors — no leakage restriction.
/// CALIBRATION: used for threshold/configuration SELECTION — structurally
/// barred from ever including HOLDOUT (see `TouchCandidateExperimentConfiguration
/// .validateSplitSafety()`).
/// HOLDOUT: must never influence threshold selection or detector changes —
/// used only for a final, single-shot, unbiased evaluation.
public enum DatasetSplit: String, Codable, Equatable, Sendable, CaseIterable {
    case development
    case calibration
    case holdout
}

// MARK: - Inclusion / exclusion (Part 7)

public enum AssessmentExclusionReason: String, Codable, Equatable, Sendable {
    case missingGroundTruth
    case missingPredictions
    case missingSynchronization
    case invalidRecording
    case malformedArtifact
    case duplicateAssessment
    case manualExclusion
    case incompatibleSchema
}

/// Never silently dropped: an assessment is either included, or explicitly
/// excluded with a named reason — there is no third, ambiguous state.
public enum AssessmentInclusionStatus: Codable, Equatable, Sendable {
    case included
    case excluded(reason: AssessmentExclusionReason)
}

// MARK: - Dataset manifest (Part 3)

/// One assessment's entry in a dataset manifest. References are plain
/// strings (filenames/paths/identifiers) — this type does not resolve or
/// read them; a separate loader does that (kept decoupled, see
/// `TouchCandidateExperimentRunner`). No player name/PII — assessment/
/// session IDs only.
public struct TouchCandidateDatasetAssessmentEntry: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var sessionId: String?
    public var captureExportReference: String?
    public var groundTruthReference: String?
    public var videoReference: String?
    /// Reuses the EXISTING `SynchronizationProvenance` enum from the
    /// real-data capture bridge — never a new, parallel "validated" concept
    /// invented here (Part 6: "do NOT invent 'validated' from capture PTS
    /// alone" — and indeed no such case exists).
    public var synchronizationStatus: SynchronizationProvenance
    public var split: DatasetSplit
    public var inclusionStatus: AssessmentInclusionStatus
    /// Optional, forward-looking only (Part 5) — never invented when player/
    /// session grouping isn't actually known. nil today, always.
    public var groupId: String?
    public var notes: String?

    public init(
        assessmentId: String, sessionId: String? = nil, captureExportReference: String? = nil,
        groundTruthReference: String? = nil, videoReference: String? = nil,
        synchronizationStatus: SynchronizationProvenance, split: DatasetSplit,
        inclusionStatus: AssessmentInclusionStatus = .included, groupId: String? = nil, notes: String? = nil
    ) {
        self.assessmentId = assessmentId
        self.sessionId = sessionId
        self.captureExportReference = captureExportReference
        self.groundTruthReference = groundTruthReference
        self.videoReference = videoReference
        self.synchronizationStatus = synchronizationStatus
        self.split = split
        self.inclusionStatus = inclusionStatus
        self.groupId = groupId
        self.notes = notes
    }
}

public enum TouchCandidateDatasetError: Error, Equatable {
    case duplicateAssessmentId(String)
}

public struct TouchCandidateDatasetManifest: Codable, Equatable, Sendable {
    public static let currentSchemaVersion = 1

    public var schemaVersion: Int
    public var datasetId: String
    public var createdAt: Date
    public var description: String?
    public var assessments: [TouchCandidateDatasetAssessmentEntry]

    /// Duplicate assessment IDs are rejected UNCONDITIONALLY (across ANY
    /// split, not just within one) — never silently deduplicated or allowed.
    public init(
        schemaVersion: Int = TouchCandidateDatasetManifest.currentSchemaVersion, datasetId: String,
        createdAt: Date = Date(), description: String? = nil, assessments: [TouchCandidateDatasetAssessmentEntry]
    ) throws {
        var seen = Set<String>()
        for entry in assessments {
            guard seen.insert(entry.assessmentId).inserted else {
                throw TouchCandidateDatasetError.duplicateAssessmentId(entry.assessmentId)
            }
        }
        self.schemaVersion = schemaVersion
        self.datasetId = datasetId
        self.createdAt = createdAt
        self.description = description
        self.assessments = assessments
    }

    /// Part 5: forward-looking leakage guardrail. Returns every `groupId`
    /// that appears across MORE THAN ONE split — a real, checkable signal
    /// once `groupId` is ever actually populated; harmless (empty) today
    /// since nothing sets it yet.
    public func crossSplitGroupLeakage() -> [String] {
        var splitsByGroup: [String: Set<DatasetSplit>] = [:]
        for entry in assessments {
            guard let groupId = entry.groupId else { continue }
            splitsByGroup[groupId, default: []].insert(entry.split)
        }
        return splitsByGroup.filter { $0.value.count > 1 }.map { $0.key }.sorted()
    }

    /// A real content-addressed dataset identity (2026-09-28, calibration
    /// experiment protocol phase) — fills a gap the original `datasetId`
    /// plain string left open: two different manifests could otherwise
    /// share the same `datasetId` label with no way to detect it. Order-
    /// independent (entries are sorted by `assessmentId` before hashing) so
    /// manifest ordering never changes identity. Only the fields that
    /// materially define WHICH data is in the dataset are included —
    /// `notes` and `createdAt` are labels, not content.
    public var contentFingerprint: String {
        let entryDescriptions = assessments
            .sorted { $0.assessmentId < $1.assessmentId }
            .map { entry -> String in
                let inclusion: String
                switch entry.inclusionStatus {
                case .included: inclusion = "included"
                case .excluded(let reason): inclusion = "excluded:\(reason.rawValue)"
                }
                return "\(entry.assessmentId)|\(entry.sessionId ?? "")|\(entry.synchronizationStatus.rawValue)"
                    + "|\(entry.split.rawValue)|\(inclusion)|\(entry.groupId ?? "")"
            }
        let canonical = "schema=\(schemaVersion)|dataset=\(datasetId)|entries=\(entryDescriptions.joined(separator: ";"))"
        let digest = SHA256.hash(data: Data(canonical.utf8))
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}
