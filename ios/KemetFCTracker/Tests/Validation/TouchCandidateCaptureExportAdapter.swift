//
//  TouchCandidateCaptureExportAdapter.swift
//  KemetFCTrackerTests
//
//  Converts a production `TouchCandidateCaptureExport` (Sources/Tracking/)
//  into the existing `PredictionDataset`/`TimestampMapping` types the
//  ground-truth evaluator already consumes (Part 13, 2026-09-28 real-data
//  capture bridge). Pure field mapping only — never re-implements matching,
//  metrics, or any detector logic; the evaluator stays the single source of
//  truth for all of that.
//

import Foundation
@testable import KemetFCTracker

public enum TouchCandidateCaptureExportAdapter {
    /// `PredictionDataset` alone — timebase is carried straight through from
    /// the export (whatever `predictionTimebase` says, honestly).
    public static func predictionDataset(from export: TouchCandidateCaptureExport) -> PredictionDataset {
        PredictionDataset(
            assessmentId: export.assessmentId,
            timebase: export.predictionTimebase,
            predictions: export.predictions.map { prediction in
                TouchCandidatePredictionRecord(
                    eventId: prediction.eventId, timeSeconds: prediction.interactionTimeSeconds,
                    ballTrackId: prediction.ballTrackId, speedChange: prediction.speedChange,
                    directionSimilarity: prediction.directionSimilarity,
                    minimumPlayerBallDistance: prediction.minimumPlayerBallDistance
                )
            }
        )
    }

    /// The export's own `synchronization` record, translated into a
    /// `TimestampMapping` the evaluator accepts — ONLY when the export's
    /// provenance actually claims a real, usable offset
    /// (`.measuredFromCapturePTS` or, explicitly labeled, an
    /// approximation via `.approximateHistoricalOffset`). Returns nil
    /// (never `.identical`) when no trustworthy offset exists, so a caller
    /// is forced to either supply their own mapping or accept the
    /// evaluator's own `.missingTimestampMapping` rejection — this adapter
    /// never fabricates a mapping the export itself doesn't support.
    public static func timestampMapping(from export: TouchCandidateCaptureExport) -> TimestampMapping? {
        guard let synchronization = export.synchronization, let offset = synchronization.offsetSeconds else { return nil }
        switch synchronization.provenance {
        case .measuredFromCapturePTS:
            return TimestampMapping(offsetSeconds: offset, provenance: .measuredPerSession)
        case .derivedFromRecordedAsset:
            return TimestampMapping(offsetSeconds: offset, provenance: .measuredPerSession)
        case .approximateHistoricalOffset:
            return TimestampMapping(offsetSeconds: offset, provenance: .approximatedFromHistoricalMeasurement)
        case .unavailable:
            return nil
        }
    }
}
