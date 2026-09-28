//
//  MovTimebaseComparator.swift
//  KemetFCTrackerTests
//
//  Combines a `TouchCandidateCaptureExport`'s `capturePtsOriginSeconds` with
//  a `MovTimebaseProbeResult`'s first video sample PTS into a RAW TIMEBASE
//  COMPARISON — explicitly NEVER a validated synchronization, and NEVER
//  automatically promoted into a `TimestampMapping`. Never combines
//  artifacts from two different assessments (identity is checked where
//  possible and a mismatch is rejected, not silently compared). Never
//  substitutes the historical ~0.15-0.2s approximation for a real value.
//

import Foundation
@testable import KemetFCTracker

public enum IdentityCheck: Equatable, Sendable {
    /// The capture export's `video.filename` matched the probed file's own
    /// name.
    case matched
    /// They did not match — the comparison is REJECTED, not silently
    /// produced with a warning, per instruction ("mismatched assessment
    /// identity rejected where identity is available").
    case mismatched(captureVideoFilename: String, movFilename: String)
    /// Identity could not be checked at all (e.g. no capture export
    /// supplied) — the comparison MAY still proceed, but callers must not
    /// treat this the same as a confirmed match.
    case notCheckable
}

/// A raw, unvalidated observation only — see the file header.
public struct RawTimebaseComparison: Equatable, Sendable {
    public var capturePtsOriginSeconds: Double
    public var firstMovVideoSamplePtsSeconds: Double
    /// `firstMovVideoSamplePtsSeconds - capturePtsOriginSeconds` — the SAME
    /// sign convention as `TimestampMapping.offsetSeconds` (`videoTime =
    /// analysisTime + offsetSeconds`), so that IF this value were ever
    /// promoted to a real mapping later, the sign would already be correct
    /// — but promoting it is an explicit, separate, NOT-yet-taken step.
    public var differenceSeconds: Double
    public var identityCheck: IdentityCheck
}

public enum RawTimebaseComparisonOutcome: Equatable, Sendable {
    case captureOnly(capturePtsOriginSeconds: Double)
    case movOnly(firstMovVideoSamplePtsSeconds: Double)
    case neitherAvailable
    case rejectedIdentityMismatch(captureVideoFilename: String, movFilename: String)
    case compared(RawTimebaseComparison)
}

public enum MovTimebaseComparator {
    /// `capture` may be nil (no export available) or an export with no
    /// synchronization anchor at all (e.g. `.unavailable` provenance from
    /// the live pipeline) — both are treated as "capture side absent".
    /// Non-finite values (NaN/±infinity) on either side are treated as
    /// absent, never used in arithmetic.
    public static func compare(capture: TouchCandidateCaptureExport?, movProbe: MovTimebaseProbeResult?) -> RawTimebaseComparisonOutcome {
        let captureValue = usable(capture?.synchronization?.capturePtsOriginSeconds)
        let movValue = usable(movProbe?.firstVideoSamplePtsSeconds)

        switch (captureValue, movValue) {
        case (nil, nil):
            return .neitherAvailable
        case (let c?, nil):
            return .captureOnly(capturePtsOriginSeconds: c)
        case (nil, let m?):
            return .movOnly(firstMovVideoSamplePtsSeconds: m)
        case (let c?, let m?):
            let identity = identityCheck(capture: capture, movProbe: movProbe)
            if case .mismatched(let captureFilename, let movFilename) = identity {
                return .rejectedIdentityMismatch(captureVideoFilename: captureFilename, movFilename: movFilename)
            }
            return .compared(RawTimebaseComparison(
                capturePtsOriginSeconds: c, firstMovVideoSamplePtsSeconds: m,
                differenceSeconds: m - c, identityCheck: identity
            ))
        }
    }

    private static func usable(_ value: Double?) -> Double? {
        guard let value, value.isFinite else { return nil }
        return value
    }

    private static func identityCheck(capture: TouchCandidateCaptureExport?, movProbe: MovTimebaseProbeResult?) -> IdentityCheck {
        guard let captureFilename = capture?.video.filename, let movProbe else { return .notCheckable }
        let movFilename = (movProbe.filePath as NSString).lastPathComponent
        return captureFilename == movFilename ? .matched : .mismatched(captureVideoFilename: captureFilename, movFilename: movFilename)
    }
}
