//
//  TouchCandidateReplayEngine.swift
//  KemetFCTrackerTests
//
//  Offline replay: TouchCandidateObservationCapture → the LIVE, UNMODIFIED
//  TouchCandidateDetector → predictions (2026-09-28, raw observation replay
//  foundation). This is the SAME real detector class production uses —
//  never a copy-pasted state machine — so live and replay behavior can
//  never diverge by construction.
//
//  CONFIGURATION SCOPE (2026-09-28, configuration injection): since
//  `TouchCandidateDetector` now accepts an injected
//  `TouchCandidateDetectorConfigurationSnapshot` (defaulting to `.baseline`,
//  which live production always uses implicitly), this engine can replay
//  the SAME captured observation stream under ANY semantically valid
//  configuration — not just baseline. A configuration that fails
//  `validate()` (see that type's own doc — e.g. a negative window duration,
//  a non-finite value, or a direction-similarity threshold outside its
//  meaningful cosine range) is REJECTED, never silently substituted or
//  clamped. Producing a DIFFERENT alternate configuration is NOT itself
//  calibration, ranking, or a claim that any configuration is better — see
//  `TouchCandidateExperimentRunner` for where evaluation against ground
//  truth actually happens; this file only proves replay is trustworthy.
//

import Foundation
@testable import KemetFCTracker

public enum TouchCandidateReplayError: Error, Equatable {
    case incompatibleSchemaVersion(found: Int, expected: Int)
    /// The requested configuration failed `TouchCandidateDetectorConfigurationSnapshot.validate()`.
    case invalidConfiguration(TouchCandidateDetectorConfigurationSnapshotValidationError)
}

/// Remains a Touch Candidate — NEVER "ground truth" or "validated touches"
/// (Part 13). Reuses `ExportedTouchCandidatePrediction` (the SAME mechanical
/// projection production capture export already uses) for the event list.
public struct TouchCandidateReplayResult: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var sessionId: String?
    public var sourceCaptureSchemaVersion: Int
    public var sourceObservationCount: Int
    public var replayDetectorConfiguration: TouchCandidateDetectorConfigurationSnapshot
    public var configurationFingerprint: String
    public var events: [ExportedTouchCandidatePrediction]
    public var generatedAt: Date

    public init(
        assessmentId: String, sessionId: String?, sourceCaptureSchemaVersion: Int, sourceObservationCount: Int,
        replayDetectorConfiguration: TouchCandidateDetectorConfigurationSnapshot, configurationFingerprint: String,
        events: [ExportedTouchCandidatePrediction], generatedAt: Date = Date()
    ) {
        self.assessmentId = assessmentId
        self.sessionId = sessionId
        self.sourceCaptureSchemaVersion = sourceCaptureSchemaVersion
        self.sourceObservationCount = sourceObservationCount
        self.replayDetectorConfiguration = replayDetectorConfiguration
        self.configurationFingerprint = configurationFingerprint
        self.events = events
        self.generatedAt = generatedAt
    }
}

public enum TouchCandidateReplayEngine {
    /// Feeds `capture.observations`, IN ORDER, through a FRESH
    /// `TouchCandidateDetector(configuration:)` instance — the SAME
    /// production detector implementation, never a reimplementation of its
    /// state machine (one full epoch — matches the live "one relevant
    /// epoch per exported artifact" guarantee; a fresh instance is itself
    /// equivalent to a `reset()` boundary). The original
    /// `capture.observations` array is never mutated (value-type `struct`,
    /// `let`-only access here), and `capture.detectorConfigurationAtCapture`
    /// (what was LIVE when the observations were recorded) is left
    /// completely untouched — only this call's own `configuration`
    /// parameter (what this REPLAY uses) can differ from it.
    public static func replay(
        capture: TouchCandidateObservationCapture, configuration: TouchCandidateDetectorConfigurationSnapshot = .baseline
    ) throws -> TouchCandidateReplayResult {
        guard capture.schemaVersion == TouchCandidateObservationCapture.currentSchemaVersion else {
            throw TouchCandidateReplayError.incompatibleSchemaVersion(found: capture.schemaVersion, expected: TouchCandidateObservationCapture.currentSchemaVersion)
        }
        do {
            try configuration.validate()
        } catch let validationError as TouchCandidateDetectorConfigurationSnapshotValidationError {
            throw TouchCandidateReplayError.invalidConfiguration(validationError)
        }

        let detector = TouchCandidateDetector(configuration: configuration)
        var events: [TouchCandidateEvent] = []
        for observation in capture.observations {
            let (analysis, coMovement) = observation.reconstructedSnapshots()
            if let event = detector.observe(analysis: analysis, coMovement: coMovement) {
                events.append(event)
            }
        }

        return TouchCandidateReplayResult(
            assessmentId: capture.assessmentId, sessionId: capture.sessionId,
            sourceCaptureSchemaVersion: capture.schemaVersion, sourceObservationCount: capture.observations.count,
            replayDetectorConfiguration: configuration, configurationFingerprint: configuration.fingerprint,
            events: events.map { ExportedTouchCandidatePrediction(from: $0) }
        )
    }
}

// MARK: - Dataset integration (Part 14) — reuses existing types, no duplication

extension TouchCandidatePredictionRecord {
    /// Mechanical field copy — NOT a re-derivation. Bridges the production
    /// capture-export prediction type to the evaluator's own prediction
    /// type, which are otherwise structurally identical but intentionally
    /// separate (Sources/ vs. Tests/Validation/ — see each type's own doc).
    init(exported: ExportedTouchCandidatePrediction) {
        self.init(
            eventId: exported.eventId, timeSeconds: exported.interactionTimeSeconds, ballTrackId: exported.ballTrackId,
            speedChange: exported.speedChange, directionSimilarity: exported.directionSimilarity,
            minimumPlayerBallDistance: exported.minimumPlayerBallDistance
        )
    }
}

extension TouchCandidateReplayResult {
    /// Converts directly into the EXISTING `PredictionDataset` the
    /// evaluator/experiment runner already consume — no second evaluator,
    /// no duplicated matching/metrics logic anywhere in this file.
    public func toPredictionDataset(timebase: Timebase) -> PredictionDataset {
        PredictionDataset(assessmentId: assessmentId, timebase: timebase, predictions: events.map { TouchCandidatePredictionRecord(exported: $0) })
    }
}
