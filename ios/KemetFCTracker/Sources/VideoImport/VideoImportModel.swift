//
//  VideoImportModel.swift
//  KemetFCTracker
//
//  The rules and words of an imported-video assessment. Foundation only: plain facts in, verdicts and text
//  out, so every rule is testable without a camera, a device, a photo library or the app.
//
//  An imported video is NOT a camera recording: no assessment timer ran, so the camera recording's
//  "the file must cover the time the assessment ran" rule (RecordingIntegrity) does not apply. Its own check
//  asks only whether the file is a readable, playable video with a usable size, length, frame size and
//  frame rate. It never opens a file, never calls the network and never changes a record.
//

import Foundation

// BEGIN VideoImportModel
/// Where the assessment video came from. Both sources end up as the same kind of pending record and take the
/// same upload path; this is the only difference between them.
enum AssessmentVideoSource: String, Equatable {
    case cameraRecording = "CAMERA_RECORDING"
    case importedVideo = "IMPORTED_VIDEO"

    var displayName: String {
        switch self {
        case .cameraRecording: return "Recorded Video"
        case .importedVideo: return "Uploaded Video"
        }
    }
}

/// What was measured on the stored copy of an imported video.
struct ImportedVideoMeasurement: Equatable {
    var fileExists: Bool
    var sizeBytes: Int64
    var isReadable: Bool
    var isPlayable: Bool
    var hasProtectedContent: Bool
    var hasVideoTrack: Bool
    var durationSeconds: Double
    /// Upright (display) size in pixels, after the video's own rotation is applied.
    var width: Int
    var height: Int
    /// Nominal frames per second of the video track; 0 when the file does not say.
    var frameRate: Double
}

enum ImportedVideoIntegrity {
    enum Verdict: Equatable {
        case valid
        case invalid(String)
    }

    /// Smallest and largest sane frame sizes and frame rates. These reject a corrupt header, not a poor video.
    static let minimumDimension = 64
    static let maximumDimension = 16_384
    static let maximumFrameRate = 1_000.0
    /// The existing upload path rejects anything above this (VideoUploadManager.maxUploadSizeBytes).
    static let maximumUploadBytes: Int64 = 2 * 1024 * 1024 * 1024

    static func evaluate(_ m: ImportedVideoMeasurement) -> Verdict {
        guard m.fileExists else { return .invalid("The video file could not be found.") }
        guard m.sizeBytes > 0 else { return .invalid("The video file is empty.") }
        guard m.isReadable else { return .invalid("The video file cannot be read.") }
        guard !m.hasProtectedContent else { return .invalid("This video is copy-protected and cannot be analysed.") }
        guard m.hasVideoTrack else { return .invalid("This file has no video track.") }
        guard m.isPlayable else { return .invalid("This video cannot be played, so it cannot be analysed.") }
        guard m.durationSeconds.isFinite, m.durationSeconds > 0 else { return .invalid("The video has no length.") }
        guard m.width >= minimumDimension, m.height >= minimumDimension,
              m.width <= maximumDimension, m.height <= maximumDimension else {
            return .invalid("The video's picture size is not valid.")
        }
        guard m.frameRate.isFinite, m.frameRate > 0, m.frameRate <= maximumFrameRate else {
            return .invalid("The video does not report a usable frame rate.")
        }
        guard m.sizeBytes <= maximumUploadBytes else {
            return .invalid("This video is larger than the 2 GB upload limit.")
        }
        return .valid
    }
}

/// What the review screen shows for an imported video, taken from the stored record.
struct ImportedVideoReviewFacts: Equatable {
    var fileExists: Bool
    /// The integrity check's own verdict, kept on the record when the video was imported.
    var integrityProblem: String?
    var durationSeconds: Double?
    var width: Int?
    var height: Int?
    var frameRate: Double?
    var sizeBytes: Int64?
    var coachSaved: Bool
}

enum ImportedVideoReviewModel {
    enum Status: Equatable {
        case ready
        case problem(String)
    }

    static func status(_ facts: ImportedVideoReviewFacts) -> Status {
        if let problem = facts.integrityProblem { return .problem(problem) }
        if !facts.fileExists { return .problem("The video file is missing from this iPhone. Choose the video again.") }
        return .ready
    }

    /// SAVE ASSESSMENT is offered only for a healthy video that is on this iPhone and not yet saved.
    static func canSave(_ facts: ImportedVideoReviewFacts) -> Bool {
        status(facts) == .ready && !facts.coachSaved
    }

    static func statusWord(_ status: Status) -> String {
        switch status {
        case .ready: return "READY"
        case .problem: return "PROBLEM"
        }
    }

    static func durationText(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds >= 0 else { return "—" }
        let whole = Int(seconds)
        return String(format: "%02d:%02d", whole / 60, whole % 60)
    }

    /// "1080 × 1920 · 60 fps". Unknown parts show a dash, never a made-up number.
    static func resolutionText(width: Int?, height: Int?, frameRate: Double?) -> String {
        guard let width, let height, width > 0, height > 0 else { return "—" }
        var text = "\(width) × \(height)"
        if let frameRate, frameRate.isFinite, frameRate > 0 {
            text += " · \(Int(frameRate.rounded())) fps"
        }
        return text
    }

    static func sizeText(_ bytes: Int64?) -> String {
        guard let bytes, bytes >= 0 else { return "—" }
        let megabytes = Double(bytes) / 1_048_576
        return megabytes >= 1024 ? String(format: "%.2f GB", megabytes / 1024) : String(format: "%.1f MB", megabytes)
    }

    /// Nothing has been analysed. The screen never shows WAITING, PROCESSING or a score.
    static let analysisLabel = "NOT AVAILABLE YET"
    static let reviewNote = "Nothing has been uploaded yet. Save to keep this video as the assessment; it then uploads in the background."
    static let savedNote = "You can leave now. The video uploads in the background and does not need this screen."
}
// END VideoImportModel
