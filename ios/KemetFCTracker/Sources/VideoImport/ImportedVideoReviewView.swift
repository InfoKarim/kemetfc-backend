//
//  ImportedVideoReviewView.swift
//  KemetFCTracker
//
//  The two screens that follow importing a video in PLAYER ASSESSMENT:
//
//    REVIEW  - player, video source "Uploaded Video", length, picture size, file size, VIDEO STATUS READY or
//              PROBLEM, SAVE ASSESSMENT and Cancel
//    SAVED   - ASSESSMENT SAVED, the upload line and the analysis line
//
//  It reads the pending record the importer already wrote. SAVE ASSESSMENT hands the record to the existing
//  upload code (through RootView); Cancel removes only the fresh copy. No analysis has happened, and the
//  screen says so; it never shows a score, a rating or a made-up analysis status.
//

import SwiftUI

struct ImportedVideoReviewView: View {
    @ObservedObject var store: PendingAssessmentStore
    @ObservedObject var mode: VideoFirstAssessmentMode

    let recordId: String
    let onSave: () -> Void
    let onCancel: () -> Void
    let onRetryUpload: () -> Void
    let onDone: () -> Void
    let onViewPendingUploads: () -> Void
    var onSelectPlayerInVideo: (() -> Void)? = nil

    private var record: PendingAssessmentRecord? { store.record(id: recordId) }

    var body: some View {
        Group {
            if let record {
                let facts = Self.facts(of: record, store: store)
                if record.coachSavedAt != nil && ImportedVideoReviewModel.status(facts) == .ready {
                    saved(record, facts)
                } else {
                    review(record, facts)
                }
            } else {
                ContentUnavailableView("Video Not Found", systemImage: "questionmark.circle")
            }
        }
    }

    // MARK: review

    private func review(_ record: PendingAssessmentRecord, _ facts: ImportedVideoReviewFacts) -> some View {
        let status = ImportedVideoReviewModel.status(facts)
        return VStack(spacing: 22) {
            header(icon: status == .ready ? "video.badge.checkmark" : "exclamationmark.triangle.fill",
                   color: status == .ready ? .green : .red, title: status == .ready ? "REVIEW" : "VIDEO PROBLEM")

            VStack(spacing: 14) {
                details(record, facts)
                row("Video status", ImportedVideoReviewModel.statusWord(status))
                if case .problem(let reason) = status {
                    Text(reason).font(.footnote).foregroundStyle(.red).multilineTextAlignment(.center)
                }
                if let warning = record.integrityWarning {
                    Text(warning).font(.footnote).foregroundStyle(.orange).multilineTextAlignment(.center)
                }
                Text(ImportedVideoReviewModel.reviewNote)
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding()
            .background(.thinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .padding(.horizontal)

            Spacer()

            VStack(spacing: 12) {
                Button("SAVE ASSESSMENT") { onSave() }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
                    .disabled(!ImportedVideoReviewModel.canSave(facts))
                Button("Cancel") { onCancel() }
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal)
            .padding(.bottom, 24)
        }
    }

    // MARK: saved

    private func saved(_ record: PendingAssessmentRecord, _ facts: ImportedVideoReviewFacts) -> some View {
        let upload = VideoFirstReviewView.facts(of: record, store: store)
        return VStack(spacing: 22) {
            header(icon: "checkmark.circle.fill", color: .green, title: "ASSESSMENT SAVED")

            VStack(spacing: 14) {
                details(record, facts)
                row("Video upload", VideoFirstAssessmentModel.uploadLabel(upload))
                row("Player in video", record.targetSelections?.isEmpty == false ? "SELECTED" : "NOT SELECTED YET")
                row("AI analysis", ImportedVideoReviewModel.analysisLabel)
                if let lastError = record.lastError, record.uploadState != .uploaded {
                    Text(lastError).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                Text(ImportedVideoReviewModel.savedNote)
                    .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding()
            .background(.thinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .padding(.horizontal)

            Spacer()

            VStack(spacing: 12) {
                if let onSelectPlayerInVideo {
                    Button("SELECT PLAYER IN VIDEO", action: onSelectPlayerInVideo)
                        .buttonStyle(.borderedProminent)
                        .frame(maxWidth: .infinity)
                }
                if record.isRetryable {
                    Button("Retry Upload", action: onRetryUpload)
                        .buttonStyle(.borderedProminent)
                        .frame(maxWidth: .infinity)
                }
                Button("View Pending Uploads", action: onViewPendingUploads)
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
                Button("Done", action: onDone)
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal)
            .padding(.bottom, 24)
        }
    }

    // MARK: helpers

    private func header(icon: String, color: Color, title: String) -> some View {
        VStack(spacing: 6) {
            Text("PLAYER ASSESSMENT").font(.caption).bold().foregroundStyle(.secondary)
            Image(systemName: icon).font(.system(size: 44)).foregroundStyle(color)
            Text(title).font(.title2).bold()
        }
        .padding(.top, 24)
    }

    @ViewBuilder
    private func details(_ record: PendingAssessmentRecord, _ facts: ImportedVideoReviewFacts) -> some View {
        row("Player", record.playerName)
        row("Video source", AssessmentVideoSource.importedVideo.displayName)
        row("Duration", ImportedVideoReviewModel.durationText(facts.durationSeconds))
        row("Resolution", ImportedVideoReviewModel.resolutionText(width: facts.width, height: facts.height, frameRate: facts.frameRate))
        row("File size", ImportedVideoReviewModel.sizeText(facts.sizeBytes))
    }

    /// Facts for the model, from the stored record (the integrity verdict is the importer's own).
    static func facts(of record: PendingAssessmentRecord, store: PendingAssessmentStore) -> ImportedVideoReviewFacts {
        let parts = (record.resolution ?? "").split(separator: "x").compactMap { Int($0) }
        return ImportedVideoReviewFacts(
            fileExists: store.fileExistsSync(for: record),
            integrityProblem: record.integrityFailure,
            durationSeconds: record.durationSeconds,
            width: parts.count == 2 ? parts[0] : nil,
            height: parts.count == 2 ? parts[1] : nil,
            frameRate: record.frameRateFps,
            sizeBytes: record.fileSizeBytes,
            coachSaved: record.coachSavedAt != nil
        )
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).bold().multilineTextAlignment(.trailing)
        }
    }
}
