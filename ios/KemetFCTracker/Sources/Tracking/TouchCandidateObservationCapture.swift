//
//  TouchCandidateObservationCapture.swift
//  KemetFCTracker
//
//  Raw per-tick observation record + versioned capture artifact for OFFLINE
//  Touch Candidate REPLAY (2026-09-28, raw observation replay foundation).
//  This file stores ONLY the exact fields `TouchCandidateDetector
//  .observe(analysis:coMovement:)` itself reads — confirmed by direct
//  source audit, not assumed — and nothing else: no frames, images, pixel
//  buffers, pose landmarks, player names, or other PII. Values are
//  MECHANICAL copies of the live snapshot fields — nil stays nil, no
//  cleaning/interpolation/smoothing/repair of any kind. This file performs
//  no detection, no camera/recording access, and does not alter Touch
//  Candidate semantics.
//

import Foundation

// MARK: - Per-tick observation record (Part 2)

/// Exactly the 7 fields `TouchCandidateDetector.observe(...)` reads from
/// `PlayerLiveAnalysisSnapshot`/`PlayerBallCoMovementSnapshot` — verified
/// against the current detector source, not guessed.
public struct TouchCandidateObservationRecord: Codable, Equatable, Sendable {
    public var timestampSeconds: TimeInterval
    public var ballVisible: Bool
    /// nil means "not currently valid" — the SAME semantics as
    /// `PlayerLiveAnalysisSnapshot.ballVelocity`. Never substituted with
    /// `(0,0)`.
    public var ballVelocity: NormalizedPoint?
    public var normalizedPlayerBallDistance: Double?
    public var ballProximityState: BallProximityState
    public var ballTrackId: Int?
    public var coMovementState: PlayerBallCoMovementState

    public init(
        timestampSeconds: TimeInterval, ballVisible: Bool, ballVelocity: NormalizedPoint?,
        normalizedPlayerBallDistance: Double?, ballProximityState: BallProximityState,
        ballTrackId: Int?, coMovementState: PlayerBallCoMovementState
    ) {
        self.timestampSeconds = timestampSeconds
        self.ballVisible = ballVisible
        self.ballVelocity = ballVelocity
        self.normalizedPlayerBallDistance = normalizedPlayerBallDistance
        self.ballProximityState = ballProximityState
        self.ballTrackId = ballTrackId
        self.coMovementState = coMovementState
    }

    /// Mechanical projection from the EXACT two live inputs
    /// `TouchCandidateDetector.observe(...)` consumes — copies fields only,
    /// never re-derives anything (same principle as
    /// `TouchCandidatePredictionRecord`/`ExportedTouchCandidatePrediction`).
    public init(analysis: PlayerLiveAnalysisSnapshot, coMovement: PlayerBallCoMovementSnapshot) {
        self.timestampSeconds = analysis.timestamp
        self.ballVisible = analysis.ballVisible
        self.ballVelocity = analysis.ballVelocity
        self.normalizedPlayerBallDistance = analysis.normalizedPlayerBallDistance
        self.ballProximityState = analysis.ballProximityState
        self.ballTrackId = analysis.ballTrackId
        self.coMovementState = coMovement.state
    }

    /// Reconstructs the minimal live snapshot PAIR needed to feed
    /// `TouchCandidateDetector.observe(...)` identically. Every field the
    /// detector does NOT read (confirmed by source audit) is filled with a
    /// clearly-labeled placeholder — never a value that could plausibly
    /// change detector behavior if the audit is ever wrong; if the detector
    /// is ever changed to read one of these, replay will silently start
    /// using placeholder data, which is exactly why the accompanying
    /// baseline-equivalence tests (comparing live vs. replay on the SAME
    /// observation stream) exist as an ongoing regression guard.
    public func reconstructedSnapshots() -> (analysis: PlayerLiveAnalysisSnapshot, coMovement: PlayerBallCoMovementSnapshot) {
        let analysis = PlayerLiveAnalysisSnapshot(
            timestamp: timestampSeconds,
            trackingState: .locked, // placeholder — not read by TouchCandidateDetector
            trackingConfidence: nil, // placeholder
            movementState: .uncertain, // placeholder
            ballVisible: ballVisible,
            ballConfidence: nil, // placeholder — not read by TouchCandidateDetector
            ballProximityState: ballProximityState,
            normalizedPlayerBallDistance: normalizedPlayerBallDistance,
            ballVelocity: ballVelocity,
            ballTrackId: ballTrackId
        )
        let coMovement = PlayerBallCoMovementSnapshot(timestamp: timestampSeconds, state: coMovementState)
        return (analysis, coMovement)
    }
}

// MARK: - Versioned capture artifact (Part 4)

public enum TouchCandidateObservationCaptureError: Error, Equatable {
    case nonFiniteTimestamp(index: Int)
    case negativeTimestamp(index: Int)
    case nonMonotonicTimestamp(index: Int)
    case nonFiniteVelocity(index: Int)
    case nonFiniteDistance(index: Int)
}

/// One assessment's full raw observation stream, with the detector
/// configuration that was ACTIVE (provenance only — never a restriction on
/// what configuration may later replay it) when the observations were
/// collected.
public struct TouchCandidateObservationCapture: Codable, Equatable, Sendable {
    public static let currentSchemaVersion = 1

    public var schemaVersion: Int
    public var assessmentId: String
    public var sessionId: String?
    public var observationTimebase: Timebase
    /// Provenance only — see the type-level doc. Reusing this snapshot to
    /// RESTRICT replay to only this configuration would be a misuse of this
    /// field.
    public var detectorConfigurationAtCapture: TouchCandidateDetectorConfigurationSnapshot?
    public var detectorConfigurationFingerprint: String?
    public var observations: [TouchCandidateObservationRecord]
    public var createdAt: Date

    /// Validates ordering/finiteness (Part 5) at construction — rejects
    /// malformed input rather than silently repairing it. An EMPTY
    /// `observations` array is explicitly valid (e.g. the ball never
    /// appeared this session) — never itself an error.
    public init(
        schemaVersion: Int = TouchCandidateObservationCapture.currentSchemaVersion,
        assessmentId: String, sessionId: String? = nil, observationTimebase: Timebase = .analysisSeconds,
        detectorConfigurationAtCapture: TouchCandidateDetectorConfigurationSnapshot? = nil,
        observations: [TouchCandidateObservationRecord], createdAt: Date = Date()
    ) throws {
        var lastTimestamp: TimeInterval?
        for (index, observation) in observations.enumerated() {
            guard observation.timestampSeconds.isFinite else {
                throw TouchCandidateObservationCaptureError.nonFiniteTimestamp(index: index)
            }
            guard observation.timestampSeconds >= 0 else {
                throw TouchCandidateObservationCaptureError.negativeTimestamp(index: index)
            }
            if let last = lastTimestamp, observation.timestampSeconds < last {
                throw TouchCandidateObservationCaptureError.nonMonotonicTimestamp(index: index)
            }
            // Equal timestamps ARE permitted — matches audited live
            // semantics exactly: TouchCandidateDetector.observe() itself
            // treats `timestamp <= last` as a no-op, not an error, so a
            // duplicate live timestamp is real, tolerated data, not
            // corruption.
            lastTimestamp = observation.timestampSeconds
            if let velocity = observation.ballVelocity, !velocity.x.isFinite || !velocity.y.isFinite {
                throw TouchCandidateObservationCaptureError.nonFiniteVelocity(index: index)
            }
            if let distance = observation.normalizedPlayerBallDistance, !distance.isFinite {
                throw TouchCandidateObservationCaptureError.nonFiniteDistance(index: index)
            }
        }
        self.schemaVersion = schemaVersion
        self.assessmentId = assessmentId
        self.sessionId = sessionId
        self.observationTimebase = observationTimebase
        self.detectorConfigurationAtCapture = detectorConfigurationAtCapture
        self.detectorConfigurationFingerprint = detectorConfigurationAtCapture?.fingerprint
        self.observations = observations
        self.createdAt = createdAt
    }
}

// MARK: - Deterministic JSON

public enum TouchCandidateObservationCaptureIO {
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
