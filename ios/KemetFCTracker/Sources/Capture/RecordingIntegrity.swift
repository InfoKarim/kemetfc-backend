//
//  RecordingIntegrity.swift
//  KemetFCTracker
//
//  Decides whether a FINALIZED recording may be reported as a success.
//  Foundation-only (plain values in, verdict out) so the rule is unit-testable off-device.
//
//  WHY: a physical-device run produced a 0.148 s file for a 30 s assessment. AVFoundation
//  reported the stop as an error (-11818 "Recording Stopped", underlying -16414); the app
//  logged it and ignored it, "non-empty and readable" was the only file check, and the
//  coach saw "SYNCED". The finalized media is the source of truth: it must exist, contain
//  video, and cover (nearly) the whole interval the assessment ran. The UI timer is never
//  used to make a short file look long.
//

import Foundation

/// What AVFoundation reported when it finalized the file, kept as plain values.
public struct RecordingFinalizationError: Codable, Equatable, Sendable {
    public var domain: String
    public var code: Int
    public var underlyingDomain: String?
    public var underlyingCode: Int?
    /// `AVErrorRecordingSuccessfullyFinishedKey` — nil when AVFoundation did not say.
    public var reportedSuccessfullyFinished: Bool?
    public var message: String

    public init(
        domain: String, code: Int,
        underlyingDomain: String? = nil, underlyingCode: Int? = nil,
        reportedSuccessfullyFinished: Bool? = nil, message: String
    ) {
        self.domain = domain
        self.code = code
        self.underlyingDomain = underlyingDomain
        self.underlyingCode = underlyingCode
        self.reportedSuccessfullyFinished = reportedSuccessfullyFinished
        self.message = message
    }

    public init(_ error: Error) {
        let ns = error as NSError
        let underlying = ns.userInfo[NSUnderlyingErrorKey] as? NSError
        self.init(
            domain: ns.domain,
            code: ns.code,
            underlyingDomain: underlying?.domain,
            underlyingCode: underlying?.code,
            // The literal value of AVErrorRecordingSuccessfullyFinishedKey.
            reportedSuccessfullyFinished: ns.userInfo["AVErrorRecordingSuccessfullyFinishedKey"] as? Bool,
            message: ns.localizedDescription
        )
    }

    public var summary: String {
        var text = "\(domain) \(code)"
        if let underlyingDomain, let underlyingCode { text += " (underlying \(underlyingDomain) \(underlyingCode))" }
        return text
    }
}

/// What was actually measured on the finalized file.
public struct RecordingMeasurement: Equatable, Sendable {
    public var fileExists: Bool
    public var sizeBytes: Int64
    public var hasVideoTrack: Bool
    public var durationSeconds: Double

    public init(fileExists: Bool, sizeBytes: Int64, hasVideoTrack: Bool, durationSeconds: Double) {
        self.fileExists = fileExists
        self.sizeBytes = sizeBytes
        self.hasVideoTrack = hasVideoTrack
        self.durationSeconds = durationSeconds
    }
}

public enum RecordingIntegrity {
    public enum Verdict: Equatable, Sendable {
        /// The file covers the assessment. `warning` is non-nil when AVFoundation still
        /// reported an error at finalization — kept on the record, never erased by an upload.
        case valid(warning: String?)
        /// The file must not be reported as a successful recording or synced.
        case invalid(String)
    }

    /// The media must cover at least this fraction of the wall-clock interval, minus `slackSeconds`
    /// (start-up latency before the first frame, stop latency). 30 s → at least 23 s; 60 s → 47 s.
    public static let minimumCoverage = 0.8
    public static let slackSeconds = 1.0
    /// Below this wall-clock length no coverage rule applies (a tap-and-release is not a failure
    /// of the recorder), but the file must still hold video of positive length.
    public static let coverageRuleThresholdSeconds = 2.0

    public static func requiredDuration(wallSeconds: Double) -> Double {
        wallSeconds >= coverageRuleThresholdSeconds ? max(wallSeconds * minimumCoverage - slackSeconds, 0) : 0
    }

    public static func evaluate(
        measurement m: RecordingMeasurement,
        wallSeconds: Double,
        finalizationError: RecordingFinalizationError?
    ) -> Verdict {
        guard m.fileExists else {
            return .invalid("Recording file was not found after stopping — the camera may have failed to finalize it.")
        }
        guard m.sizeBytes > 0 else {
            return .invalid("Recording file is empty (0 bytes) after stopping.")
        }
        guard m.hasVideoTrack, m.durationSeconds.isFinite, m.durationSeconds > 0 else {
            return .invalid("Recorded video is not readable — it has no playable video.")
        }
        let required = requiredDuration(wallSeconds: wallSeconds)
        if m.durationSeconds < required {
            let cause = finalizationError.map { " The camera reported \($0.summary): \($0.message)." } ?? ""
            return .invalid(String(
                format: "Recording incomplete: the saved video is only %.1f s but the assessment ran for %.0f s.%@ It was not uploaded as a valid recording.",
                m.durationSeconds, wallSeconds, cause
            ))
        }
        if let finalizationError {
            if finalizationError.reportedSuccessfullyFinished != true {
                return .invalid("The camera failed to finalize the recording (\(finalizationError.summary): \(finalizationError.message)).")
            }
            return .valid(warning: "The camera reported \(finalizationError.summary) at stop, but the saved video covers the assessment.")
        }
        return .valid(warning: nil)
    }
}
