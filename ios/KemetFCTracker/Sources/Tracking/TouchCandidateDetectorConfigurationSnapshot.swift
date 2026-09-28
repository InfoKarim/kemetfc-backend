//
//  TouchCandidateDetectorConfigurationSnapshot.swift
//  KemetFCTracker
//
//  Moved here from Tests/Validation/TouchCandidateExperiment.swift
//  (2026-09-28, raw observation replay foundation) — production capture
//  code (TrackingCoordinator/AssessmentSessionViewModel) now needs this
//  type directly to record which configuration was active at capture time,
//  per "the minimal neutral data contract belongs in Sources/" (same
//  precedent as `Timebase`'s earlier move). Tests/Validation/ still sees it
//  unchanged via `@testable import`; no duplicate definition is kept there.
//

import CryptoKit
import Foundation

/// An IMMUTABLE record of the detector settings that produced (or, at
/// replay time, will produce) a given set of predictions — for
/// REPRODUCIBILITY, not for injecting configuration into the live detector
/// (which does not accept injected configuration as of this file — see the
/// 2026-09-28 configuration-injection approval-gate report). Fields are
/// exactly the 7 constants confirmed to actually affect
/// `TouchCandidateDetector`'s output.
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
    /// "optimized" or "calibrated", and this snapshot changes NOTHING
    /// about the live detector's actual behavior.
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
