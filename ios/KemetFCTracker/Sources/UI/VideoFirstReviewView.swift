//
//  VideoFirstReviewView.swift
//  KemetFCTracker
//
//  The two screens that follow a recording in the video-first PLAYER ASSESSMENT:
//
//    REVIEW  - player, video length, recording status READY or the problem, and SAVE ASSESSMENT
//    SAVED   - ASSESSMENT SAVED, the upload line and the analysis line; the coach can leave at once
//
//  Choosing the player and recording are the existing screens (PlayerSelectionView, the camera screen
//  with its Start and Stop). This view starts nothing, stops nothing and never touches the camera,
//  tracking, telemetry or the upload code. It only reads the pending record the existing engine already
//  saved, and offers the actions the classic summary already offered (Retry Upload, Pending Uploads,
//  Done).
//
//  SAVE ASSESSMENT is the coach's confirmation. By the time it is shown the recording is already
//  finalised, has passed the existing integrity check, carries the selected player's id, and is durable
//  in PendingAssessmentStore. Pressing it twice changes nothing.
//

import SwiftUI

struct VideoFirstReviewView: View {
    @ObservedObject var store: PendingAssessmentStore
    @ObservedObject var mode: VideoFirstAssessmentMode
    @EnvironmentObject private var session: AssessmentSessionViewModel

    let recordId: String
    let onRetryUpload: () -> Void
    let onDone: () -> Void
    let onViewPendingUploads: () -> Void
    /// SELECT PLAYER IN VIDEO, offered once the assessment is saved (nil hides it).
    var onSelectPlayerInVideo: (() -> Void)? = nil

    @State private var showManualMeasurements = false

    private var record: PendingAssessmentRecord? { store.record(id: recordId) }

    var body: some View {
        VStack(spacing: 22) {
            if let record {
                let facts = Self.facts(of: record, store: store)
                if mode.savedRecordIds.contains(recordId) && VideoFirstAssessmentModel.canSave(facts) {
                    saved(record, facts)
                } else {
                    review(record, facts)
                }
            } else {
                ContentUnavailableView("Recording Not Found", systemImage: "questionmark.circle")
            }
        }
        .fullScreenCover(isPresented: $showManualMeasurements) {
            // The Phase 1 manual measurement screens, kept as an advanced tool. Same page as before;
            // only reachable from here, never from Home.
            WebDashboardScreen(
                baseURL: AssessmentWebConfiguration.baseURL(fallback: session.sharedAPIClient.baseURL),
                pagePath: "v1/sprint",
                restrictToOrigin: true,
                onClose: { showManualMeasurements = false }
            )
        }
    }

    // MARK: review

    private func review(_ record: PendingAssessmentRecord, _ facts: VideoFirstAssessmentModel.RecordingFacts) -> some View {
        let status = VideoFirstAssessmentModel.reviewStatus(facts)
        return VStack(spacing: 22) {
            VStack(spacing: 6) {
                Text("PLAYER ASSESSMENT").font(.caption).bold().foregroundStyle(.secondary)
                Image(systemName: icon(for: status))
                    .font(.system(size: 44))
                    .foregroundStyle(color(for: status))
                Text(title(for: status)).font(.title2).bold()
            }
            .padding(.top, 24)

            VStack(spacing: 14) {
                row("Player", record.playerName)
                row("Video", VideoFirstAssessmentModel.durationText(facts.durationSeconds))
                row("Recording status", statusWord(status))
                if case .problem(let reason) = status {
                    Text(reason)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                    if let detail = record.integrityFailure {
                        Text(detail).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    }
                }
                if let warning = record.integrityWarning {
                    Text(warning).font(.footnote).foregroundStyle(.orange).multilineTextAlignment(.center)
                }
            }
            .padding()
            .background(.thinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .padding(.horizontal)

            Spacer()

            VStack(spacing: 12) {
                Button("SAVE ASSESSMENT") { mode.save(recordId) }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
                    .disabled(!VideoFirstAssessmentModel.canSave(facts))
                Button("Done") { finish() }
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal)
            .padding(.bottom, 24)
        }
    }

    // MARK: saved

    private func saved(_ record: PendingAssessmentRecord, _ facts: VideoFirstAssessmentModel.RecordingFacts) -> some View {
        VStack(spacing: 22) {
            VStack(spacing: 6) {
                Text("PLAYER ASSESSMENT").font(.caption).bold().foregroundStyle(.secondary)
                Image(systemName: "checkmark.circle.fill").font(.system(size: 44)).foregroundStyle(.green)
                Text("ASSESSMENT SAVED").font(.title2).bold()
            }
            .padding(.top, 24)

            VStack(spacing: 14) {
                row("Player", record.playerName)
                row("Video", VideoFirstAssessmentModel.durationText(facts.durationSeconds))
                row("Video upload", VideoFirstAssessmentModel.uploadLabel(facts))
                row("Player in video", record.targetSelections?.isEmpty == false ? "SELECTED" : "NOT SELECTED YET")
                row("AI analysis", VideoFirstAssessmentModel.analysisLabel)
                if let lastError = record.lastError, record.uploadState != .uploaded {
                    Text(lastError).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                Text(VideoFirstAssessmentModel.savedNote)
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
                Button("Manual measurements (advanced)") { showManualMeasurements = true }
                    .font(.footnote)
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                Button("Done") { finish() }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal)
            .padding(.bottom, 24)
        }
    }

    // MARK: helpers

    private func finish() {
        mode.end()
        onDone()
    }

    /// The facts the model needs, read from the pending record. The integrity verdict is the existing
    /// check's own (`integrityFailure`), and the file check is the store's own cheap existence check.
    static func facts(of record: PendingAssessmentRecord, store: PendingAssessmentStore) -> VideoFirstAssessmentModel.RecordingFacts {
        VideoFirstAssessmentModel.RecordingFacts(
            fileExists: store.fileExistsSync(for: record),
            integrityFailed: record.integrityFailure != nil,
            uploadPhase: VideoFirstAssessmentModel.UploadPhase(rawValue: phaseName(record.uploadState)) ?? .localSaved,
            durationSeconds: record.durationSeconds
        )
    }

    private static func phaseName(_ state: PendingAssessmentRecord.UploadState) -> String {
        switch state {
        case .recording: return "recording"
        case .localSaved: return "localSaved"
        case .pendingUpload: return "pendingUpload"
        case .uploading: return "uploading"
        case .uploaded: return "uploaded"
        case .failedRetryable: return "failedRetryable"
        case .failedPermanent: return "failedPermanent"
        }
    }

    private func title(for status: VideoFirstAssessmentModel.ReviewStatus) -> String {
        switch status {
        case .ready: return "REVIEW"
        case .problem: return "RECORDING PROBLEM"
        case .finishing: return "FINISHING THE RECORDING"
        }
    }

    private func statusWord(_ status: VideoFirstAssessmentModel.ReviewStatus) -> String {
        switch status {
        case .ready: return "READY"
        case .problem: return "NOT VALID"
        case .finishing: return "FINISHING"
        }
    }

    private func icon(for status: VideoFirstAssessmentModel.ReviewStatus) -> String {
        switch status {
        case .ready: return "video.badge.checkmark"
        case .problem: return "exclamationmark.triangle.fill"
        case .finishing: return "hourglass"
        }
    }

    private func color(for status: VideoFirstAssessmentModel.ReviewStatus) -> Color {
        switch status {
        case .ready: return .green
        case .problem: return .red
        case .finishing: return .orange
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).bold().multilineTextAlignment(.trailing)
        }
    }
}
