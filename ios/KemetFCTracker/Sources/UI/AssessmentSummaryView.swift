//
//  AssessmentSummaryView.swift
//  KemetFCTracker
//
//  The end-of-assessment screen — shown after Stop Assessment regardless
//  of whether the upload has succeeded yet. The local recording is
//  NEVER discarded from here; this screen only ever offers Retry Upload
//  or navigating to Pending Uploads, never a delete. The coach is never
//  forced to wait for upload completion here before moving to another
//  player — Done is always available.
//
//  Observes `store` directly (rather than taking a static record
//  snapshot) so a background auto-retry succeeding while this screen is
//  still showing refreshes it immediately — a confirmed gap found
//  during audit: the previous version took a plain struct value with no
//  live connection back to the store.
//

import SwiftUI

struct AssessmentSummaryView: View {
    @ObservedObject var store: PendingAssessmentStore
    let recordId: String
    let onRetryUpload: () -> Void
    let onDone: () -> Void
    let onViewPendingUploads: () -> Void
    let onConfirmPlayerTracked: (String) -> Void
    /// SELECT PLAYER IN VIDEO for the video-first flow (nil in the classic flow, which does not show it).
    var onSelectPlayerInVideo: (() -> Void)? = nil

    // BUG FIX (found during recording-fix verification): this dialog used to live on
    // AssessmentCameraView, triggered synchronously by the Stop button — but
    // stopAssessment() sets lastFinishedRecordingId as soon as it finishes, which
    // RootView checks with HIGHER precedence than the camera screen, so the app was
    // already switching to THIS summary screen (unmounting AssessmentCameraView, and
    // with it the dialog) before the coach could ever tap an answer — "appears and
    // disappears immediately." This screen is the one that actually stays on screen
    // after Stop, so the confirmation now lives here instead, shown once per finished
    // recording via .onAppear.
    @State private var showPlayerTrackedConfirmation = false

    /// True only when the coach came in through the video-first PLAYER ASSESSMENT. RECORD ASSESSMENT
    /// clears it, so the classic summary below is shown exactly as before for that route.
    @ObservedObject private var videoFirst = VideoFirstAssessmentMode.shared

    private var record: PendingAssessmentRecord? { store.record(id: recordId) }

    var body: some View {
        NavigationStack {
            if let record {
                content(for: record)
                    .onAppear {
                        // Only meaningful for a recording that actually has tracking
                        // telemetry to confirm — never asked for an integrity failure.
                        if record.integrityFailure == nil {
                            showPlayerTrackedConfirmation = true
                        }
                    }
                    .confirmationDialog(
                        "Was the correct player tracked?",
                        isPresented: $showPlayerTrackedConfirmation,
                        titleVisibility: .visible
                    ) {
                        Button("Yes") { onConfirmPlayerTracked("yes") }
                        Button("No") { onConfirmPlayerTracked("no") }
                        Button("Unsure") { onConfirmPlayerTracked("unsure") }
                        Button("Skip", role: .cancel) {}
                    }
            } else {
                // Should not happen in practice (the record that just
                // finished recording always exists) — never crash on a
                // missing record, show something honest instead.
                ContentUnavailableView("Recording Not Found", systemImage: "questionmark.circle")
            }
        }
    }

    @ViewBuilder
    private func content(for record: PendingAssessmentRecord) -> some View {
        if videoFirst.isActive {
            VideoFirstReviewView(
                store: store, mode: videoFirst, recordId: recordId,
                onRetryUpload: onRetryUpload, onDone: onDone, onViewPendingUploads: onViewPendingUploads,
                onSelectPlayerInVideo: onSelectPlayerInVideo
            )
        } else {
            classicContent(for: record)
        }
    }

    private func classicContent(for record: PendingAssessmentRecord) -> some View {
        VStack(spacing: 24) {
            VStack(spacing: 4) {
                Text("KEMET FC").font(.caption).bold().foregroundStyle(.secondary)
                Image(systemName: statusIcon(record))
                    .font(.system(size: 44))
                    .foregroundStyle(statusColor(record))
                    .padding(.top, 4)
                Text(record.integrityFailure == nil ? "ASSESSMENT COMPLETE" : "RECORDING FAILED")
                    .font(.title2).bold()
            }
            .padding(.top, 24)

            VStack(spacing: 14) {
                summaryRow("Player", record.playerName)
                summaryRow("Recording", durationText(record))
                summaryRow("Status", localFileStatusText(record))
                summaryRow("Upload", uploadStatusText(record))
                if let failure = record.integrityFailure {
                    Text(failure)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                } else if let lastError = record.lastError, record.uploadState != .uploaded {
                    Text(lastError)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                if let warning = record.integrityWarning {
                    Text(warning)
                        .font(.footnote)
                        .foregroundStyle(.orange)
                        .multilineTextAlignment(.center)
                }
            }
            .padding()
            .background(.thinMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 16))
            .padding(.horizontal)

            Spacer()

            VStack(spacing: 12) {
                if record.isRetryable {
                    Button("Retry Upload", action: onRetryUpload)
                        .buttonStyle(.borderedProminent)
                        .frame(maxWidth: .infinity)
                }
                Button("View Pending Uploads", action: onViewPendingUploads)
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
                Button("Done", action: onDone)
                    .buttonStyle(.bordered)
                    .frame(maxWidth: .infinity)
            }
            .padding(.horizontal)
            .padding(.bottom, 24)
        }
    }

    private func durationText(_ record: PendingAssessmentRecord) -> String {
        guard let duration = record.durationSeconds else { return "—" }
        let minutes = Int(duration) / 60
        let seconds = Int(duration) % 60
        return String(format: "%02d:%02d", minutes, seconds)
    }

    /// Truthful "Status" row — never claims the file is saved on this
    /// iPhone without a live existence check (spec: "Never claim a
    /// video is saved unless the file exists, has size greater than
    /// zero, and is AVAsset-readable" — the size/readability checks
    /// already happened once, authoritatively, at stopAssessment()/
    /// recovery time; this repeats only the cheap existence check for
    /// display, and defers to that stored verification for the rest).
    private func localFileStatusText(_ record: PendingAssessmentRecord) -> String {
        guard store.fileExistsSync(for: record) else { return "Recording file missing" }
        if record.integrityFailure != nil { return "Recording incomplete — not valid" }
        switch record.uploadState {
        case .failedPermanent:
            if let lastError = record.lastError,
               lastError.localizedCaseInsensitiveContains("not readable")
                || lastError.localizedCaseInsensitiveContains("not playable")
                || lastError.localizedCaseInsensitiveContains("corrupt") {
                return "Recording file unreadable"
            }
            return "Saved on this iPhone"
        default:
            return "Saved on this iPhone"
        }
    }

    private func uploadStatusText(_ record: PendingAssessmentRecord) -> String {
        switch record.uploadState {
        case .recording, .localSaved: return "Uploading..."
        case .pendingUpload, .failedRetryable: return "UPLOAD PENDING\nWill retry automatically"
        case .uploading: return "Uploading..."
        case .uploaded: return "SYNCED\nSecurely uploaded"
        case .failedPermanent:
            return record.integrityFailure != nil ? "Not uploaded — recording is invalid\nRecord this player again" : "Upload failed — see Pending Uploads"
        }
    }

    private func statusIcon(_ record: PendingAssessmentRecord) -> String {
        if record.integrityFailure != nil { return "exclamationmark.triangle.fill" }
        return record.uploadState == .uploaded ? "checkmark.circle.fill" : "icloud.and.arrow.up"
    }

    private func statusColor(_ record: PendingAssessmentRecord) -> Color {
        switch record.uploadState {
        case .uploaded: return .green
        case .failedPermanent: return .red
        default: return .orange
        }
    }

    private func summaryRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value).bold().multilineTextAlignment(.trailing)
        }
    }
}
