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

    private var record: PendingAssessmentRecord? { store.record(id: recordId) }

    var body: some View {
        NavigationStack {
            if let record {
                content(for: record)
            } else {
                // Should not happen in practice (the record that just
                // finished recording always exists) — never crash on a
                // missing record, show something honest instead.
                ContentUnavailableView("Recording Not Found", systemImage: "questionmark.circle")
            }
        }
    }

    private func content(for record: PendingAssessmentRecord) -> some View {
        VStack(spacing: 24) {
            VStack(spacing: 4) {
                Text("KEMET FC").font(.caption).bold().foregroundStyle(.secondary)
                Image(systemName: statusIcon(record))
                    .font(.system(size: 44))
                    .foregroundStyle(statusColor(record))
                    .padding(.top, 4)
                Text("ASSESSMENT COMPLETE")
                    .font(.title2).bold()
            }
            .padding(.top, 24)

            VStack(spacing: 14) {
                summaryRow("Player", record.playerName)
                summaryRow("Recording", durationText(record))
                summaryRow("Status", localFileStatusText(record))
                summaryRow("Upload", uploadStatusText(record))
                if let lastError = record.lastError, record.uploadState != .uploaded {
                    Text(lastError)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
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
        case .failedPermanent: return "Upload failed — see Pending Uploads"
        }
    }

    private func statusIcon(_ record: PendingAssessmentRecord) -> String {
        record.uploadState == .uploaded ? "checkmark.circle.fill" : "icloud.and.arrow.up"
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
