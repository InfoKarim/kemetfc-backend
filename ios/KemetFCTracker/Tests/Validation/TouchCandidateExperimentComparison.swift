//
//  TouchCandidateExperimentComparison.swift
//  KemetFCTrackerTests
//
//  Factual metric deltas between two experiment results (Part 14,
//  2026-09-28 dataset & calibration foundation). Deliberately contains NO
//  "winner"/"recommended"/"better" field of any kind, and computes no
//  ranking — Part 15's guardrail: this is not an optimizer, and never
//  becomes one. Humans decide what tradeoff matters.
//

import Foundation

public struct TouchCandidateExperimentComparison: Codable, Equatable, Sendable {
    public var baselineExperimentId: String
    public var candidateExperimentId: String
    public var deltaTruePositives: Int
    public var deltaFalsePositives: Int
    public var deltaFalseNegatives: Int
    /// nil whenever EITHER side's own precision/recall/F1 is nil (an
    /// undefined metric has no meaningful delta — never fabricated as 0).
    public var deltaPrecision: Double?
    public var deltaRecall: Double?
    public var deltaF1: Double?
    public var deltaMeanAbsoluteTimingErrorSeconds: Double?

    public init(
        baselineExperimentId: String, candidateExperimentId: String, deltaTruePositives: Int,
        deltaFalsePositives: Int, deltaFalseNegatives: Int, deltaPrecision: Double?,
        deltaRecall: Double?, deltaF1: Double?, deltaMeanAbsoluteTimingErrorSeconds: Double?
    ) {
        self.baselineExperimentId = baselineExperimentId
        self.candidateExperimentId = candidateExperimentId
        self.deltaTruePositives = deltaTruePositives
        self.deltaFalsePositives = deltaFalsePositives
        self.deltaFalseNegatives = deltaFalseNegatives
        self.deltaPrecision = deltaPrecision
        self.deltaRecall = deltaRecall
        self.deltaF1 = deltaF1
        self.deltaMeanAbsoluteTimingErrorSeconds = deltaMeanAbsoluteTimingErrorSeconds
    }
}

public enum TouchCandidateExperimentComparator {
    /// `candidate - baseline` for every metric — a positive delta simply
    /// means "candidate's number was higher," carrying no judgment about
    /// whether higher is good (e.g. a higher FP count is not "better").
    public static func compare(baseline: TouchCandidateExperimentResult, candidate: TouchCandidateExperimentResult) -> TouchCandidateExperimentComparison {
        func delta(_ a: Double?, _ b: Double?) -> Double? {
            guard let a, let b else { return nil }
            return b - a
        }
        return TouchCandidateExperimentComparison(
            baselineExperimentId: baseline.experimentId, candidateExperimentId: candidate.experimentId,
            deltaTruePositives: candidate.aggregate.truePositiveCount - baseline.aggregate.truePositiveCount,
            deltaFalsePositives: candidate.aggregate.falsePositiveCount - baseline.aggregate.falsePositiveCount,
            deltaFalseNegatives: candidate.aggregate.falseNegativeCount - baseline.aggregate.falseNegativeCount,
            deltaPrecision: delta(baseline.aggregate.precision, candidate.aggregate.precision),
            deltaRecall: delta(baseline.aggregate.recall, candidate.aggregate.recall),
            deltaF1: delta(baseline.aggregate.f1Score, candidate.aggregate.f1Score),
            deltaMeanAbsoluteTimingErrorSeconds: delta(
                baseline.aggregate.timing.meanAbsoluteErrorSeconds, candidate.aggregate.timing.meanAbsoluteErrorSeconds
            )
        )
    }
}
