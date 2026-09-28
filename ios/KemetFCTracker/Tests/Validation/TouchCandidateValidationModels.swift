//
//  TouchCandidateValidationModels.swift
//  KemetFCTrackerTests
//
//  Ground-truth/prediction/result data contracts for OFFLINE Touch Candidate
//  validation — see the 2026-09-28 ground-truth validation audit. Deliberately
//  compiled into the TEST target only, never the shipped app: this is offline
//  measurement tooling, not live inference, and the live TouchCandidateDetector
//  never depends on any of it.
//
//  AUDIT FINDING (do not re-derive, this is the current, verified state):
//  `TouchCandidateEvent`/`TouchCandidateEvidence` are NOT `Codable` in
//  production, and `TouchCandidateEvent`s are never persisted, never sent to
//  telemetry, and never reach the backend (confirmed: `TrackingSample`, the
//  actual struct uploaded via `TelemetryUploader.recordSample`, has no
//  touch-candidate fields at all). So offline evaluation cannot consume a
//  live `TouchCandidateEvent` after the fact — it consumes this file's own
//  `TouchCandidatePredictionRecord`, a plain, mechanical field-copy
//  projection (see the `init(from:)` below), never a re-derivation of any
//  detector logic.
//

import Foundation
@testable import KemetFCTracker

// MARK: - Timebase

/// Which clock a set of timestamps is expressed in. Never assumed identical
/// across ground truth and predictions — see `TimestampMapping`.
public enum Timebase: String, Codable, Equatable, Sendable {
    /// Seconds into the recorded `.mov` file itself.
    case videoSeconds = "video_seconds"
    /// `sessionElapsedSeconds` — the analysis pipeline's own clock (see
    /// PLAYER_ANALYSIS_V2_EVENT_DESIGN.md section 12 and the 2026-09-28
    /// audit: this has a real, measured, NOT-currently-mapped offset from
    /// the video file's own t=0, empirically ~0.15-0.2s from one historical
    /// investigation — never assume it equals `videoSeconds`).
    case analysisSeconds = "analysis_seconds"
}

// MARK: - Ground truth (Part 2)

/// Human-labeled real touches for one assessment's recorded video.
/// Deterministic, human-editable JSON; diffs cleanly in Git (encoded with
/// `.sortedKeys` — see `TouchCandidateValidationIO`).
public struct GroundTruthDataset: Codable, Equatable, Sendable {
    public var assessmentId: String
    /// Informational only — the evaluator never opens this file.
    public var videoFile: String?
    public var timebase: Timebase
    public var touches: [GroundTruthTouch]

    public init(assessmentId: String, videoFile: String? = nil, timebase: Timebase, touches: [GroundTruthTouch]) {
        self.assessmentId = assessmentId
        self.videoFile = videoFile
        self.timebase = timebase
        self.touches = touches
    }
}

public struct GroundTruthTouch: Codable, Equatable, Sendable {
    public var id: String
    public var timeSeconds: Double
    public var notes: String?
    /// Reserved for a future per-touch tolerance override — accepted and
    /// round-tripped, but NOT YET consumed by the matching algorithm (which
    /// uses only the global `TouchCandidateValidationConfiguration
    /// .matchingToleranceSeconds` this phase). Documented here rather than
    /// silently ignored, per "support optional uncertainty/tolerance later
    /// without requiring it now."
    public var toleranceSecondsOverride: Double?

    public init(id: String, timeSeconds: Double, notes: String? = nil, toleranceSecondsOverride: Double? = nil) {
        self.id = id
        self.timeSeconds = timeSeconds
        self.notes = notes
        self.toleranceSecondsOverride = toleranceSecondsOverride
    }
}

// MARK: - Predictions (Part 3)

/// A dataset of offline Touch Candidate predictions for one assessment —
/// either loaded from a file recorded during/after a real session, or built
/// in-process via `TouchCandidatePredictionRecord.init(from:)` for tests.
public struct PredictionDataset: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var timebase: Timebase
    public var predictions: [TouchCandidatePredictionRecord]

    public init(assessmentId: String, timebase: Timebase, predictions: [TouchCandidatePredictionRecord]) {
        self.assessmentId = assessmentId
        self.timebase = timebase
        self.predictions = predictions
    }
}

/// The minimum offline representation of one `TouchCandidateEvent`. Every
/// field here is a MECHANICAL COPY from the live event (see `init(from:)`)
/// — this type never re-derives, re-thresholds, or duplicates any of
/// `TouchCandidateDetector`'s own logic.
public struct TouchCandidatePredictionRecord: Codable, Equatable, Sendable {
    public var eventId: String
    /// Per the audit (Part 1, item 3): `TouchCandidateEvent.interactionTime`
    /// is the field treated as the predicted interaction time — the single
    /// instant the live detector itself designates as "the interaction."
    public var timeSeconds: Double
    public var ballTrackId: Int?
    public var speedChange: Double?
    public var directionSimilarity: Double?
    public var minimumPlayerBallDistance: Double?

    public init(
        eventId: String, timeSeconds: Double, ballTrackId: Int? = nil,
        speedChange: Double? = nil, directionSimilarity: Double? = nil, minimumPlayerBallDistance: Double? = nil
    ) {
        self.eventId = eventId
        self.timeSeconds = timeSeconds
        self.ballTrackId = ballTrackId
        self.speedChange = speedChange
        self.directionSimilarity = directionSimilarity
        self.minimumPlayerBallDistance = minimumPlayerBallDistance
    }

    /// Mechanical projection of a live `TouchCandidateEvent` — copies fields
    /// only. `timebase` is whatever clock `event.interactionTime` is
    /// actually in (`.analysisSeconds`, per the live pipeline's own
    /// convention) — the caller is responsible for recording that alongside
    /// this record in a `PredictionDataset`, never assumed here.
    public init(from event: TouchCandidateEvent) {
        self.eventId = event.id.uuidString
        self.timeSeconds = event.interactionTime
        self.ballTrackId = event.ballTrackId
        self.speedChange = event.evidence.speedChange
        self.directionSimilarity = event.evidence.directionSimilarity
        self.minimumPlayerBallDistance = event.evidence.minimumPlayerBallDistance
    }
}

// MARK: - Timestamp mapping (Part 4 — CRITICAL, see the audit)

/// An EXPLICIT, required adapter between two timebases — never a silent
/// assumption. `videoTime = analysisTime + offsetSeconds`.
public struct TimestampMapping: Equatable, Sendable {
    public enum Provenance: String, Codable, Equatable, Sendable {
        /// Ground truth and predictions already share the SAME timebase —
        /// no real mapping is being applied (`offsetSeconds` must be 0).
        case identical
        /// A single, historical, EMPIRICALLY-MEASURED offset from one past
        /// recording-lifecycle investigation (~0.15-0.2s, the gap between
        /// `wireTracking()`'s `frameDelegate` assignment and the real
        /// `didStartRecordingTo` callback) — an approximation, not a
        /// per-session measurement, not validated across devices or runs.
        /// See PLAYER_ANALYSIS_V2_EVENT_DESIGN.md section 12.
        case approximatedFromHistoricalMeasurement
        /// A real, per-session offset captured at record time. NOT
        /// implemented anywhere in this codebase as of this audit — using
        /// this provenance value asserts the caller captured it themselves
        /// via some other means.
        case measuredPerSession
    }

    public var offsetSeconds: Double
    public var provenance: Provenance

    public init(offsetSeconds: Double, provenance: Provenance) {
        self.offsetSeconds = offsetSeconds
        self.provenance = provenance
    }

    /// The only mapping that requires no real adapter — both sides already
    /// share the same clock.
    public static let identical = TimestampMapping(offsetSeconds: 0, provenance: .identical)
}

// MARK: - Configuration (Part 5/6)

public struct TouchCandidateValidationConfiguration: Codable, Equatable, Sendable {
    /// UNVALIDATED VALIDATION CONSTANT — the maximum |timing error|, in
    /// seconds (after timebase mapping), for a prediction to be eligible to
    /// match a ground-truth touch at all. Never tuned to make a specific
    /// evaluation's metrics look better; any change must be justified
    /// independently of a particular result.
    public var matchingToleranceSeconds: Double

    public init(matchingToleranceSeconds: Double = 0.30) {
        self.matchingToleranceSeconds = matchingToleranceSeconds
    }
}

// MARK: - Result models (Part 7/8)

public struct MatchedEventPair: Codable, Equatable, Sendable {
    public var groundTruthId: String
    public var groundTruthTimeSeconds: Double
    public var predictionEventId: String
    /// The prediction's timestamp AFTER applying the timestamp mapping, in
    /// the SAME timebase as `groundTruthTimeSeconds`.
    public var predictedTimeSeconds: Double
    /// `predictedTimeSeconds - groundTruthTimeSeconds`.
    public var signedTimingErrorSeconds: Double
    public var absoluteTimingErrorSeconds: Double
}

public struct UnmatchedGroundTruth: Codable, Equatable, Sendable {
    public var groundTruthId: String
    public var timeSeconds: Double
}

public struct UnmatchedPrediction: Codable, Equatable, Sendable {
    public var predictionEventId: String
    /// The prediction's timestamp AFTER mapping, in the ground truth's
    /// timebase, for direct comparability in reports.
    public var mappedTimeSeconds: Double
    public var record: TouchCandidatePredictionRecord
}

/// nil, never 0, for any statistic with no matched events to compute it from.
public struct TimingStatistics: Codable, Equatable, Sendable {
    public var meanSignedErrorSeconds: Double?
    public var meanAbsoluteErrorSeconds: Double?
    public var medianAbsoluteErrorSeconds: Double?
    public var maximumAbsoluteErrorSeconds: Double?

    public static let unavailable = TimingStatistics(
        meanSignedErrorSeconds: nil, meanAbsoluteErrorSeconds: nil,
        medianAbsoluteErrorSeconds: nil, maximumAbsoluteErrorSeconds: nil
    )
}

/// One session's validation result. No 0-100 "AI score" anywhere.
public struct TouchCandidateValidationResult: Codable, Equatable, Sendable {
    public var assessmentId: String
    public var groundTruthCount: Int
    public var predictionCount: Int
    public var truePositiveCount: Int
    public var falsePositiveCount: Int
    public var falseNegativeCount: Int
    /// nil exactly when `predictionCount == 0` (undefined, never 0).
    public var precision: Double?
    /// nil exactly when `groundTruthCount == 0` (undefined, never 0).
    public var recall: Double?
    /// nil whenever `precision` or `recall` is nil, OR both are defined but
    /// sum to exactly 0 (a real 0/0 case: some predictions and some ground
    /// truth exist, but zero of either overlap them at all — never silently
    /// reported as 0).
    public var f1Score: Double?
    public var timing: TimingStatistics
    public var matches: [MatchedEventPair]
    public var falsePositives: [UnmatchedPrediction]
    public var falseNegatives: [UnmatchedGroundTruth]
    public var configuration: TouchCandidateValidationConfiguration
    public var timestampMapping: TimestampMappingRecord
}

/// `Codable` mirror of `TimestampMapping` (which itself is not `Codable` —
/// it's a construction-time input, not meant to round-trip — but the result
/// that USED it should record what was used, for the report).
public struct TimestampMappingRecord: Codable, Equatable, Sendable {
    public var offsetSeconds: Double
    public var provenance: TimestampMapping.Provenance

    public init(_ mapping: TimestampMapping) {
        self.offsetSeconds = mapping.offsetSeconds
        self.provenance = mapping.provenance
    }
}

// MARK: - Aggregation (Part 9)

public struct TouchCandidateAggregateValidationResult: Codable, Equatable, Sendable {
    public var sessionCount: Int
    public var groundTruthCount: Int
    public var predictionCount: Int
    public var truePositiveCount: Int
    public var falsePositiveCount: Int
    public var falseNegativeCount: Int
    public var precision: Double?
    public var recall: Double?
    public var f1Score: Double?
    /// Aggregated over EVERY matched pair from EVERY session combined — not
    /// an average of per-session timing statistics.
    public var timing: TimingStatistics
    public var perSession: [TouchCandidateValidationResult]
}

// MARK: - Errors

public enum TouchCandidateValidationError: Error, Equatable {
    /// Ground truth and predictions declare DIFFERENT timebases, but the
    /// supplied `TimestampMapping` claims `.identical` provenance — that
    /// combination is self-contradictory and is rejected rather than
    /// silently treating the two clocks as the same.
    case missingTimestampMapping(groundTruthTimebase: Timebase, predictionTimebase: Timebase)
    case duplicateGroundTruthId(String)
    case duplicatePredictionId(String)
}
