//
//  VideoFirstAssessment.swift
//  KemetFCTracker
//
//  What the video-first Player Assessment says after a recording stops. Foundation only: it takes plain
//  facts about a recording and returns words and yes/no answers, so every rule can be tested without a
//  camera, a device or the app.
//
//  It never starts, stops or inspects a recording, never opens a file, never calls the network and never
//  changes a pending record. The recording itself is the existing engine (CameraCaptureManager,
//  AssessmentCameraView, RecordingIntegrity, PendingAssessmentStore, the upload manager); this file only
//  describes what that engine already decided.
//

import Foundation

// BEGIN VideoFirstAssessmentModel
enum VideoFirstAssessmentModel {
    /// The stored upload progress of a recording, copied from `PendingAssessmentRecord.UploadState`.
    enum UploadPhase: String, Equatable {
        case recording, localSaved, pendingUpload, uploading, uploaded, failedRetryable, failedPermanent
    }

    /// Facts about one finished recording, taken as they are from the pending record. Nothing here is
    /// judged again: `integrityFailed` is the existing integrity check's own verdict.
    struct RecordingFacts: Equatable {
        var fileExists: Bool
        var integrityFailed: Bool
        var uploadPhase: UploadPhase
        var durationSeconds: Double?
    }

    enum ReviewStatus: Equatable {
        /// The recording finished, passed the existing integrity check and is on this iPhone.
        case ready
        /// Not valid evidence. Saving is blocked and the reason is shown.
        case problem(String)
        /// Still being finalised.
        case finishing
    }

    static func reviewStatus(_ facts: RecordingFacts) -> ReviewStatus {
        if facts.integrityFailed {
            return .problem("This recording did not pass the integrity check, so it cannot be saved as an assessment. Record this player again.")
        }
        if !facts.fileExists {
            return .problem("The recording file is missing from this iPhone. Record this player again.")
        }
        if facts.uploadPhase == .recording { return .finishing }
        return .ready
    }

    /// SAVE ASSESSMENT is offered only for a finished, healthy recording that is on this iPhone.
    static func canSave(_ facts: RecordingFacts) -> Bool {
        reviewStatus(facts) == .ready
    }

    /// "03:07" style. A recording with no known length shows a dash, never a made-up number.
    static func durationText(_ seconds: Double?) -> String {
        guard let seconds, seconds.isFinite, seconds >= 0 else { return "—" }
        let whole = Int(seconds)
        return String(format: "%02d:%02d", whole / 60, whole % 60)
    }

    /// The upload line on the saved screen. The upload runs by itself and never blocks the coach.
    static func uploadLabel(_ facts: RecordingFacts) -> String {
        if facts.integrityFailed { return "NOT UPLOADED (recording is not valid)" }
        switch facts.uploadPhase {
        case .recording, .localSaved: return "LOCAL"
        case .pendingUpload, .failedRetryable: return "PENDING"
        case .uploading: return "UPLOADING"
        case .uploaded: return "UPLOADED"
        case .failedPermanent: return "UPLOAD FAILED (kept on this iPhone)"
        }
    }

    /// Analysis does not exist yet, so the screen says so. It never shows WAITING or PROCESSING for a
    /// pipeline that is not running, and never shows a score.
    static let analysisLabel = "NOT AVAILABLE YET"

    static let savedNote = "You can leave now. The video uploads in the background and does not need this screen."
}
// END VideoFirstAssessmentModel
