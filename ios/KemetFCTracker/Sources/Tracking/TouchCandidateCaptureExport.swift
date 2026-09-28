//
//  TouchCandidateCaptureExport.swift
//  KemetFCTracker
//
//  The MINIMAL NEUTRAL DATA CONTRACT for exporting one assessment's Touch
//  Candidate predictions for offline ground-truth validation (see the
//  2026-09-28 real-data capture bridge audit). This file is pure data +
//  one pure builder function — no recording, no camera, no AVFoundation, no
//  disk I/O, no upload. It does NOT decide WHEN export happens (that
//  requires a lifecycle hook in recording-adjacent code, explicitly NOT
//  implemented this phase — see the audit report). The evaluator and its
//  metrics stay in Tests/Validation/, never here (Part 5).
//

import Foundation

/// Which clock a set of timestamps is expressed in. Never assumed identical
/// across ground truth and predictions — see the ground-truth validation
/// foundation's `TimestampMapping`. Moved here (2026-09-28, real-data
/// capture bridge) from Tests/Validation/ per the "minimal neutral data
/// contract belongs in Sources/" rule — production export code needs it
/// directly; the Tests/Validation module still sees it via `@testable
/// import KemetFCTracker`, so its own former duplicate definition was
/// removed rather than kept as a second source of truth.
public enum Timebase: String, Codable, Equatable, Sendable {
    /// Seconds into the recorded `.mov` file itself.
    case videoSeconds = "video_seconds"
    /// `sessionElapsedSeconds` — the analysis pipeline's own clock. Has a
    /// real, measured, NOT-currently-mapped offset from the video file's
    /// own t=0 (see PLAYER_ANALYSIS_V2_EVENT_DESIGN.md section 12 and this
    /// phase's audit) — never assume it equals `videoSeconds`.
    case analysisSeconds = "analysis_seconds"
}

/// How a `TimebaseSynchronizationRecord`'s offset was obtained. An
/// approximation must NEVER be labeled as measured (Part 4).
public enum SynchronizationProvenance: String, Codable, Equatable, Sendable {
    /// A true per-session offset, derived by comparing the live capture
    /// clock anchor to the finished `.mov`'s own readable sample timestamps.
    /// NOT producible by this file alone — requires an offline step reading
    /// the finished asset (not implemented this phase).
    case measuredFromCapturePTS
    /// Derived by post-processing the recorded asset directly (e.g. reading
    /// its first sample's own presentation timestamp) without a live anchor.
    case derivedFromRecordedAsset
    /// The single, historical, empirically-measured approximation from one
    /// past investigation (~0.15-0.2s) — explicitly NOT a per-session
    /// measurement. Must never be silently treated as trustworthy.
    case approximateHistoricalOffset
    /// No synchronization information could be captured for this session at
    /// all. Explicit, never a silent zero offset (Part 11).
    case unavailable
}

/// Raw synchronization anchors for one assessment — captured (or not) at
/// live-analysis time, in whatever units are actually available. Does NOT
/// itself claim `videoOriginSeconds`/`offsetSeconds` are known unless
/// `provenance` says so.
public struct TimebaseSynchronizationRecord: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var analysisTimebase: Timebase
    public var videoTimebase: Timebase
    /// `FrameForwarder.sessionStartTime`'s own value (seconds), in the
    /// analysis pipeline's video-data-output capture-clock domain — nil
    /// when unavailable. NOT yet compared against the recorded asset.
    /// Deliberately spelled "Pts" (not "PTS"): `JSONEncoder`/`JSONDecoder`'s
    /// `.convertToSnakeCase`/`.convertFromSnakeCase` strategies are NOT
    /// reliable round-trip inverses of each other for an all-caps acronym —
    /// `capturePTSOriginSeconds` encodes fine but decodes back to a
    /// DIFFERENT camelCase spelling, silently losing the value (a real bug,
    /// caught by `testMeasuredSynchronizationRecordSerializesAndRoundTrips`
    /// during this phase's own test run — verified, not theoretical).
    public var capturePtsOriginSeconds: Double?
    /// Known only once an offline step reads the finished `.mov` — always
    /// nil at export time this phase.
    public var videoOriginSeconds: Double?
    /// Known only when `provenance` is `.measuredFromCapturePTS`,
    /// `.derivedFromRecordedAsset`, or (explicitly labeled as an
    /// approximation) `.approximateHistoricalOffset`. nil when
    /// `.unavailable`.
    public var offsetSeconds: Double?
    public var provenance: SynchronizationProvenance
    public var createdAt: Date

    public init(
        assessmentId: String, analysisTimebase: Timebase, videoTimebase: Timebase,
        capturePtsOriginSeconds: Double?, videoOriginSeconds: Double? = nil,
        offsetSeconds: Double?, provenance: SynchronizationProvenance, createdAt: Date = Date()
    ) {
        self.assessmentId = assessmentId
        self.analysisTimebase = analysisTimebase
        self.videoTimebase = videoTimebase
        self.capturePtsOriginSeconds = capturePtsOriginSeconds
        self.videoOriginSeconds = videoOriginSeconds
        self.offsetSeconds = offsetSeconds
        self.provenance = provenance
        self.createdAt = createdAt
    }
}

public struct VideoReference: Codable, Equatable, Sendable {
    public var filename: String
    public var durationSeconds: Double?

    public init(filename: String, durationSeconds: Double? = nil) {
        self.filename = filename
        self.durationSeconds = durationSeconds
    }
}

/// A mechanical field-copy projection of one `TouchCandidateEvent` — never a
/// re-derivation of any detector logic (same principle as
/// `TouchCandidatePredictionRecord` in the ground-truth validation
/// foundation; this type exists separately because it must live in
/// `Sources/`, not `Tests/`, to be constructible from production code).
public struct ExportedTouchCandidatePrediction: Codable, Equatable, Sendable {
    public var eventId: String
    /// Per the existing audit finding: `TouchCandidateEvent.interactionTime`
    /// unmodified — analysis-timebase seconds, NEVER overwritten with a
    /// video-relative value (Part 9). Any video-relative time is derived
    /// only later, offline, as a SEPARATE value.
    public var interactionTimeSeconds: Double
    public var ballTrackId: Int?
    public var speedChange: Double?
    public var directionSimilarity: Double?
    public var minimumPlayerBallDistance: Double?

    public init(from event: TouchCandidateEvent) {
        self.eventId = event.id.uuidString
        self.interactionTimeSeconds = event.interactionTime
        self.ballTrackId = event.ballTrackId
        self.speedChange = event.evidence.speedChange
        self.directionSimilarity = event.evidence.directionSimilarity
        self.minimumPlayerBallDistance = event.evidence.minimumPlayerBallDistance
    }
}

/// The top-level local JSON artifact for one assessment (Part 8). An empty
/// `predictions` array is valid data (no touches predicted), never treated
/// as an export failure (Part 11 D).
public struct TouchCandidateCaptureExport: Codable, Equatable, Sendable {
    public static let currentSchemaVersion = 1

    public var schemaVersion: Int
    public var assessmentId: String
    public var sessionId: String?
    public var video: VideoReference
    public var predictionTimebase: Timebase
    public var synchronization: TimebaseSynchronizationRecord?
    public var predictions: [ExportedTouchCandidatePrediction]

    public init(
        schemaVersion: Int = TouchCandidateCaptureExport.currentSchemaVersion,
        assessmentId: String, sessionId: String?, video: VideoReference,
        predictionTimebase: Timebase, synchronization: TimebaseSynchronizationRecord?,
        predictions: [ExportedTouchCandidatePrediction]
    ) {
        self.schemaVersion = schemaVersion
        self.assessmentId = assessmentId
        self.sessionId = sessionId
        self.video = video
        self.predictionTimebase = predictionTimebase
        self.synchronization = synchronization
        self.predictions = predictions
    }

    /// Pure builder — no disk I/O, no recording/camera access, no side
    /// effects. Safe to call from anywhere that already has these values.
    /// `synchronizationAnchorSeconds` is caller-supplied (e.g. from
    /// `FrameForwarder.sessionStartTime`, once/if that is exposed) — this
    /// function never reaches into recording-adjacent state itself.
    public static func build(
        assessmentId: String, sessionId: String?, videoFilename: String, videoDurationSeconds: Double?,
        events: [TouchCandidateEvent], synchronizationAnchorSeconds: Double?
    ) -> TouchCandidateCaptureExport {
        let synchronization: TimebaseSynchronizationRecord? = synchronizationAnchorSeconds.map { anchor in
            TimebaseSynchronizationRecord(
                assessmentId: assessmentId, analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
                capturePtsOriginSeconds: anchor, videoOriginSeconds: nil,
                offsetSeconds: nil, // NOT known at export time — see the provenance doc above.
                provenance: .unavailable // Honest: having an anchor alone does not yet mean a real offset is known.
            )
        }
        return TouchCandidateCaptureExport(
            assessmentId: assessmentId, sessionId: sessionId,
            video: VideoReference(filename: videoFilename, durationSeconds: videoDurationSeconds),
            predictionTimebase: .analysisSeconds, synchronization: synchronization,
            predictions: events.map { ExportedTouchCandidatePrediction(from: $0) }
        )
    }
}

// MARK: - Deterministic JSON (mirrors TouchCandidateValidationIO's conventions)

public enum TouchCandidateCaptureExportIO {
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
