//
//  TouchCandidateEvaluator.swift
//  KemetFCTrackerTests
//
//  Offline evaluator: ground truth + predictions + timestamp mapping →
//  TouchCandidateValidationResult. Pure measurement — does not implement or
//  alter any Touch Candidate detection logic, and the live detector never
//  depends on this file (Part 14). See TouchCandidateValidationModels.swift
//  for the data contracts and their rationale.
//
//  MATCHING ALGORITHM (Part 5):
//  Ground truth and predictions are each sorted by time, predictions are
//  mapped into the ground truth's timebase, then matched with a dynamic-
//  program over the two sorted sequences (the standard O(n*m) sequence-
//  alignment recurrence): at each step, either skip the next ground-truth
//  event, skip the next prediction, or match them (only when their absolute
//  timing error is <= `matchingToleranceSeconds`). The objective is
//  LEXICOGRAPHIC: (1) maximize the number of matches, (2) among all
//  maximum-cardinality matchings, minimize total absolute timing error.
//  Nearest-neighbor-per-item greedy matching is NOT used, because it can
//  produce a globally worse (fewer-match) assignment — see
//  `testGloballyOptimalAssignmentCaseWhereGreedyNearestMatchingFails` for a
//  verified, worked counterexample. Ties are broken deterministically during
//  backtrace with a fixed priority: prefer matching over skipping a
//  prediction over skipping a ground-truth event.
//
//  This DP only ever considers ORDER-PRESERVING (non-crossing) matchings —
//  a ground-truth event earlier in time can never match a prediction later
//  in time than a prediction already matched to a LATER ground-truth event.
//  This is a deliberate, reasoned restriction, not an oversight: for 1-D
//  events on a timeline, a "crossing" assignment can be uncrossed without
//  increasing total cost (a standard rearrangement-inequality argument), and
//  real football-touch cadence (events spaced well beyond typical timing
//  jitter) makes a scenario where a crossing match is required to satisfy
//  the per-pair tolerance, while the corresponding non-crossing one is not,
//  vanishingly unlikely. Documented here rather than silently assumed.
//
import Foundation

public enum TouchCandidateEvaluator {
    public static func evaluate(
        groundTruth: GroundTruthDataset,
        predictions: PredictionDataset,
        mapping: TimestampMapping = .identical,
        configuration: TouchCandidateValidationConfiguration = TouchCandidateValidationConfiguration()
    ) throws -> TouchCandidateValidationResult {
        if groundTruth.timebase != predictions.timebase, mapping.provenance == .identical {
            throw TouchCandidateValidationError.missingTimestampMapping(
                groundTruthTimebase: groundTruth.timebase, predictionTimebase: predictions.timebase
            )
        }
        try assertUniqueIds(groundTruth.touches.map { $0.id }, error: TouchCandidateValidationError.duplicateGroundTruthId)
        try assertUniqueIds(predictions.predictions.map { $0.eventId }, error: TouchCandidateValidationError.duplicatePredictionId)

        // Sort (Part 12, test 24: "unsorted input") — the matching algorithm
        // requires temporal order; callers must never be required to
        // pre-sort themselves.
        let truths = groundTruth.touches.sorted { $0.timeSeconds < $1.timeSeconds }
        let preds = predictions.predictions.sorted { $0.timeSeconds < $1.timeSeconds }
        // Map every prediction's time into the ground truth's timebase ONCE.
        let mappedPredictionTimes = preds.map { $0.timeSeconds + mapping.offsetSeconds }

        let n = truths.count
        let m = preds.count
        let tolerance = configuration.matchingToleranceSeconds

        // dp[i][j]: best (matchCount, totalCost) using truths[0..<i], preds[0..<j].
        var dp = Array(repeating: Array(repeating: DPCell.zero, count: m + 1), count: n + 1)
        if n > 0 && m > 0 {
            for i in 1...n {
                for j in 1...m {
                    var best = DPCell.better(dp[i - 1][j], dp[i][j - 1])
                    let error = mappedPredictionTimes[j - 1] - truths[i - 1].timeSeconds
                    if isWithinTolerance(error, tolerance) {
                        let matched = DPCell(matchCount: dp[i - 1][j - 1].matchCount + 1, totalCost: dp[i - 1][j - 1].totalCost + abs(error))
                        best = DPCell.better(best, matched)
                    }
                    dp[i][j] = best
                }
            }
        }

        // Deterministic backtrace: at each cell, prefer (in order) a valid
        // match, then skipping a prediction, then skipping a ground-truth
        // event — whichever reproduces this cell's own value.
        var matches: [(truthIndex: Int, predIndex: Int)] = []
        var matchedGroundTruth = Set<Int>()
        var matchedPredictions = Set<Int>()
        var i = n, j = m
        while i > 0, j > 0 {
            let error = mappedPredictionTimes[j - 1] - truths[i - 1].timeSeconds
            if isWithinTolerance(error, tolerance),
               dp[i][j] == DPCell(matchCount: dp[i - 1][j - 1].matchCount + 1, totalCost: dp[i - 1][j - 1].totalCost + abs(error)) {
                matches.append((i - 1, j - 1))
                matchedGroundTruth.insert(i - 1)
                matchedPredictions.insert(j - 1)
                i -= 1; j -= 1
            } else if dp[i][j] == dp[i][j - 1] {
                j -= 1
            } else {
                i -= 1
            }
        }
        matches.reverse()

        let matchedPairs: [MatchedEventPair] = matches.map { pair in
            let truth = truths[pair.truthIndex]
            let mappedPredTime = mappedPredictionTimes[pair.predIndex]
            let signed = mappedPredTime - truth.timeSeconds
            return MatchedEventPair(
                groundTruthId: truth.id, groundTruthTimeSeconds: truth.timeSeconds,
                predictionEventId: preds[pair.predIndex].eventId, predictedTimeSeconds: mappedPredTime,
                signedTimingErrorSeconds: signed, absoluteTimingErrorSeconds: abs(signed)
            )
        }
        let falseNegatives: [UnmatchedGroundTruth] = (0..<n).filter { !matchedGroundTruth.contains($0) }.map {
            UnmatchedGroundTruth(groundTruthId: truths[$0].id, timeSeconds: truths[$0].timeSeconds)
        }
        let falsePositives: [UnmatchedPrediction] = (0..<m).filter { !matchedPredictions.contains($0) }.map {
            UnmatchedPrediction(predictionEventId: preds[$0].eventId, mappedTimeSeconds: mappedPredictionTimes[$0], record: preds[$0])
        }

        let tp = matchedPairs.count
        let fp = falsePositives.count
        let fn = falseNegatives.count
        let precision: Double? = (tp + fp) > 0 ? Double(tp) / Double(tp + fp) : nil
        let recall: Double? = (tp + fn) > 0 ? Double(tp) / Double(tp + fn) : nil
        let f1: Double?
        if let precision, let recall, (precision + recall) > 0 {
            f1 = 2 * precision * recall / (precision + recall)
        } else {
            f1 = nil
        }

        return TouchCandidateValidationResult(
            assessmentId: groundTruth.assessmentId,
            groundTruthCount: n, predictionCount: m,
            truePositiveCount: tp, falsePositiveCount: fp, falseNegativeCount: fn,
            precision: precision, recall: recall, f1Score: f1,
            timing: timingStatistics(for: matchedPairs),
            matches: matchedPairs, falsePositives: falsePositives, falseNegatives: falseNegatives,
            configuration: configuration, timestampMapping: TimestampMappingRecord(mapping)
        )
    }

    // MARK: - Aggregation (Part 9)

    /// Sums TP/FP/FN across sessions FIRST, then computes overall precision/
    /// recall/F1 from those sums — NEVER averages per-session rates. Timing
    /// statistics are computed over every matched pair from every session,
    /// combined into one set.
    public static func aggregate(_ results: [TouchCandidateValidationResult]) -> TouchCandidateAggregateValidationResult {
        let tp = results.reduce(0) { $0 + $1.truePositiveCount }
        let fp = results.reduce(0) { $0 + $1.falsePositiveCount }
        let fn = results.reduce(0) { $0 + $1.falseNegativeCount }
        let precision: Double? = (tp + fp) > 0 ? Double(tp) / Double(tp + fp) : nil
        let recall: Double? = (tp + fn) > 0 ? Double(tp) / Double(tp + fn) : nil
        let f1: Double?
        if let precision, let recall, (precision + recall) > 0 {
            f1 = 2 * precision * recall / (precision + recall)
        } else {
            f1 = nil
        }
        let allMatches = results.flatMap { $0.matches }
        return TouchCandidateAggregateValidationResult(
            sessionCount: results.count,
            groundTruthCount: results.reduce(0) { $0 + $1.groundTruthCount },
            predictionCount: results.reduce(0) { $0 + $1.predictionCount },
            truePositiveCount: tp, falsePositiveCount: fp, falseNegativeCount: fn,
            precision: precision, recall: recall, f1Score: f1,
            timing: timingStatistics(for: allMatches),
            perSession: results
        )
    }

    // MARK: - Private

    private struct DPCell: Equatable {
        var matchCount: Int
        var totalCost: Double
        static let zero = DPCell(matchCount: 0, totalCost: 0)
        /// Lexicographic: more matches wins outright; a tie in match count
        /// is broken by lower total cost; a tie in both deterministically
        /// keeps `a` (the first argument), so callers control tie-break
        /// order by argument position.
        static func better(_ a: DPCell, _ b: DPCell) -> DPCell {
            if a.matchCount != b.matchCount { return a.matchCount > b.matchCount ? a : b }
            return a.totalCost <= b.totalCost ? a : b
        }
    }

    /// A tiny epsilon guards the boundary against ordinary floating-point
    /// representation error (e.g. `1.30 - 1.0` is not bit-exact `0.3` in
    /// binary floating point) — NOT a change to the semantic meaning of
    /// `matchingToleranceSeconds`, which stays exactly what the
    /// configuration says; this only prevents the boundary itself from
    /// being incorrectly rejected by representation noise many orders of
    /// magnitude smaller than any realistic tolerance value.
    private static let toleranceEpsilon = 1e-9

    private static func isWithinTolerance(_ error: Double, _ tolerance: Double) -> Bool {
        abs(error) <= tolerance + toleranceEpsilon
    }

    private static func assertUniqueIds(_ ids: [String], error: (String) -> TouchCandidateValidationError) throws {
        var seen = Set<String>()
        for id in ids {
            if !seen.insert(id).inserted { throw error(id) }
        }
    }

    private static func timingStatistics(for matches: [MatchedEventPair]) -> TimingStatistics {
        guard !matches.isEmpty else { return .unavailable }
        let signed = matches.map { $0.signedTimingErrorSeconds }
        let absolute = matches.map { $0.absoluteTimingErrorSeconds }.sorted()
        let meanSigned = signed.reduce(0, +) / Double(signed.count)
        let meanAbsolute = absolute.reduce(0, +) / Double(absolute.count)
        let median: Double
        let mid = absolute.count / 2
        if absolute.count % 2 == 0 {
            median = (absolute[mid - 1] + absolute[mid]) / 2
        } else {
            median = absolute[mid]
        }
        return TimingStatistics(
            meanSignedErrorSeconds: meanSigned, meanAbsoluteErrorSeconds: meanAbsolute,
            medianAbsoluteErrorSeconds: median, maximumAbsoluteErrorSeconds: absolute.last
        )
    }
}

// MARK: - Report generation (Part 11)

public enum TouchCandidateReportFormatter {
    /// Simple Markdown summary — never fabricates a number: any nil metric
    /// is printed as "undefined", never coerced to 0.
    public static func markdown(for result: TouchCandidateValidationResult) -> String {
        func fmt(_ v: Double?, _ digits: Int = 3) -> String { v.map { String(format: "%.\(digits)f", $0) } ?? "undefined" }

        var lines: [String] = []
        lines.append("Assessment: \(result.assessmentId)")
        lines.append("Ground truth touches: \(result.groundTruthCount)")
        lines.append("Predictions: \(result.predictionCount)")
        lines.append("")
        lines.append("TP: \(result.truePositiveCount)")
        lines.append("FP: \(result.falsePositiveCount)")
        lines.append("FN: \(result.falseNegativeCount)")
        lines.append("")
        lines.append("Precision: \(fmt(result.precision))")
        lines.append("Recall: \(fmt(result.recall))")
        lines.append("F1: \(fmt(result.f1Score))")
        lines.append("")
        lines.append("Mean signed timing error (s): \(fmt(result.timing.meanSignedErrorSeconds))")
        lines.append("Mean absolute timing error (s): \(fmt(result.timing.meanAbsoluteErrorSeconds))")
        lines.append("Median absolute timing error (s): \(fmt(result.timing.medianAbsoluteErrorSeconds))")
        lines.append("Maximum absolute timing error (s): \(fmt(result.timing.maximumAbsoluteErrorSeconds))")
        lines.append("")
        lines.append("Timestamp mapping: offset=\(fmt(result.timestampMapping.offsetSeconds))s, provenance=\(result.timestampMapping.provenance.rawValue)")
        lines.append("Matching tolerance (s): \(fmt(result.configuration.matchingToleranceSeconds))")
        lines.append("")
        lines.append("FALSE POSITIVES")
        if result.falsePositives.isEmpty {
            lines.append("(none)")
        } else {
            for fpItem in result.falsePositives {
                lines.append("- \(fpItem.predictionEventId) at \(fmt(fpItem.mappedTimeSeconds))s"
                    + " (speedChange=\(fmt(fpItem.record.speedChange)), directionSimilarity=\(fmt(fpItem.record.directionSimilarity)))")
            }
        }
        lines.append("")
        lines.append("FALSE NEGATIVES")
        if result.falseNegatives.isEmpty {
            lines.append("(none)")
        } else {
            for fn in result.falseNegatives {
                lines.append("- \(fn.groundTruthId) at \(fmt(fn.timeSeconds))s")
            }
        }
        return lines.joined(separator: "\n")
    }
}

// MARK: - JSON I/O

public enum TouchCandidateValidationIO {
    /// Deterministic, human-editable, diff-friendly output: sorted keys,
    /// pretty-printed, no locale-dependent number formatting quirks
    /// (Foundation's `JSONEncoder` uses `.` decimal separators regardless of
    /// locale).
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        encoder.keyEncodingStrategy = .convertToSnakeCase
        return encoder
    }

    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }
}
