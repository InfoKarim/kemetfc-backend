//
//  VideoFirstAssessmentMode.swift
//  KemetFCTracker
//
//  Remembers, in memory only, that the coach reached the recording flow through PLAYER ASSESSMENT, so the
//  screen shown after Stop is the video-first review/save screen instead of the classic summary. It
//  carries no player, no recording and no data of its own: the player and the recording live in the
//  existing session and PendingAssessmentStore. It is cleared whenever the coach finishes, and starts
//  off after every app launch. RECORD ASSESSMENT clears it, so that button behaves exactly as before.
//

import Combine
import Foundation

@MainActor
final class VideoFirstAssessmentMode: ObservableObject {
    static let shared = VideoFirstAssessmentMode()

    @Published private(set) var isActive = false
    /// Recording ids the coach has confirmed with SAVE ASSESSMENT in this run. Saving twice is a no-op.
    @Published private(set) var savedRecordIds: Set<String> = []

    /// The video source the coach chose for this assessment: nil until they choose RECORD VIDEO or UPLOAD VIDEO.
    @Published private(set) var sourceChoice: AssessmentVideoSource?
    /// The imported video waiting for review / save, once one has been brought in and checked.
    @Published private(set) var importedRecordId: String?

    func begin() {
        isActive = true
        sourceChoice = nil
        importedRecordId = nil
    }
    func end() {
        isActive = false
        sourceChoice = nil
        importedRecordId = nil
    }

    /// RECORD VIDEO: the existing camera flow takes over, unchanged.
    func chooseRecording() { sourceChoice = .cameraRecording }
    /// UPLOAD VIDEO finished importing and checking a file.
    func importCompleted(recordId: String) {
        sourceChoice = .importedVideo
        importedRecordId = recordId
    }
    /// Back to the source choice (cancel before save).
    func returnToSourceChoice() {
        sourceChoice = nil
        importedRecordId = nil
    }

    /// Returns true only the first time a recording is confirmed.
    @discardableResult
    func save(_ recordId: String) -> Bool {
        savedRecordIds.insert(recordId).inserted
    }
}
