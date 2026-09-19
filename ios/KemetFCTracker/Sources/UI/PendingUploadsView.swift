//
//  PendingUploadsView.swift
//  KemetFCTracker
//
//  "Pending Assessments" — a small, simple operational screen, not a
//  large admin feature. Shows every recent recording (not only pending
//  ones) so the coach can see at a glance whether anything still exists
//  only on this phone. Reads PendingAssessmentStore directly — the same
//  durable queue stopAssessment()/retryUpload() write to — so this list
//  is always the real on-disk state, never a separate in-memory guess.
//
//  Wording is derived from a REAL, live file-existence check
//  (store.fileExistsSync(for:)) combined with uploadState — never from
//  uploadState alone. A physical-device audit found FAILED_PERMANENT
//  unconditionally displaying "Saved locally" even for records whose
//  video file was never found at all; this view no longer makes that
//  claim without checking.
//

import SwiftUI

struct PendingUploadsView: View {
    @ObservedObject var store: PendingAssessmentStore
    let onRetry: (String) -> Void
    let onDelete: (String) -> Void

    @State private var pendingDeleteRecord: PendingAssessmentRecord?

    private var sortedRecords: [PendingAssessmentRecord] {
        store.records.sorted { $0.startTime > $1.startTime }
    }

    var body: some View {
        NavigationStack {
            List {
                if let persistenceError = store.lastPersistenceError {
                    Section {
                        Text(persistenceError)
                            .font(.caption)
                            .foregroundStyle(.orange)
                    }
                }
                if sortedRecords.isEmpty {
                    ContentUnavailableView(
                        "No Assessments Yet",
                        systemImage: "checkmark.circle",
                        description: Text("Recorded assessments will appear here.")
                    )
                } else {
                    ForEach(sortedRecords) { record in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(record.playerName).font(.headline)
                                Spacer()
                                statusBadge(record)
                            }
                            Text(record.startTime, style: .date) + Text(" ") + Text(record.startTime, style: .time)
                            Text(statusLine(record))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            if let lastError = record.lastError, record.uploadState != .uploaded {
                                Text(lastError)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                            if record.isRetryable {
                                Button("Retry") { onRetry(record.id) }
                                    .buttonStyle(.bordered)
                                    .controlSize(.small)
                            }
                        }
                        .padding(.vertical, 4)
                        .swipeActions(edge: .trailing, allowsFullSwipe: record.uploadState != .recording) {
                            if record.uploadState != .recording {
                                Button(role: .destructive) {
                                    pendingDeleteRecord = record
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Pending Assessments")
            .confirmationDialog(
                deleteConfirmationTitle,
                isPresented: Binding(
                    get: { pendingDeleteRecord != nil },
                    set: { if !$0 { pendingDeleteRecord = nil } }
                ),
                titleVisibility: .visible
            ) {
                Button("Delete Video", role: .destructive) {
                    if let pendingDeleteRecord {
                        onDelete(pendingDeleteRecord.id)
                    }
                    pendingDeleteRecord = nil
                }
                Button("Cancel", role: .cancel) {
                    pendingDeleteRecord = nil
                }
            }
        }
    }

    private var deleteConfirmationTitle: String {
        guard let pendingDeleteRecord else { return "Delete this video?" }
        return pendingDeleteRecord.uploadState == .uploaded
            ? "Delete \(pendingDeleteRecord.playerName)'s video? It has already been synced to KEMET FC, but this only removes the copy on this phone."
            : "Delete \(pendingDeleteRecord.playerName)'s video? It has not finished uploading — this recording will be lost permanently."
    }

    /// Truthful wording (spec: "Do not display 'Saved locally' when the
    /// file is absent or unreadable") — always checks the REAL file,
    /// never infers presence purely from uploadState.
    private func statusLine(_ record: PendingAssessmentRecord) -> String {
        let fileExists = store.fileExistsSync(for: record)

        switch record.uploadState {
        case .recording:
            return "Recording"
        case .localSaved, .pendingUpload:
            return fileExists ? "Saved locally · Upload pending" : "Recording file missing"
        case .uploading:
            return fileExists ? "Uploading" : "Recording file missing"
        case .failedRetryable:
            return fileExists ? "Saved locally · Will retry" : "Recording file missing"
        case .failedPermanent:
            guard fileExists else { return "Recording file missing" }
            if let lastError = record.lastError,
               lastError.localizedCaseInsensitiveContains("not readable")
                || lastError.localizedCaseInsensitiveContains("not playable")
                || lastError.localizedCaseInsensitiveContains("corrupt") {
                return "Recording file unreadable"
            }
            return "Recovery required"
        case .uploaded:
            // Trusted from the moment it transitioned to UPLOADED, which
            // only ever happens after a genuine backend /complete
            // success (see AssessmentSessionViewModel.finalizeAndUpload)
            // — re-verifying against the server on every list render
            // would be excessive; the verification already happened at
            // the moment that matters.
            return "Synced and verified"
        }
    }

    private func statusBadge(_ record: PendingAssessmentRecord) -> some View {
        Text(badgeText(for: record.uploadState))
            .font(.caption2).bold()
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(badgeColor(for: record.uploadState).opacity(0.15))
            .foregroundStyle(badgeColor(for: record.uploadState))
            .clipShape(Capsule())
    }

    private func badgeText(for state: PendingAssessmentRecord.UploadState) -> String {
        switch state {
        case .recording: return "RECORDING"
        case .localSaved: return "SAVED"
        case .pendingUpload: return "PENDING"
        case .uploading: return "UPLOADING"
        case .failedRetryable: return "RETRYING"
        case .failedPermanent: return "FAILED"
        case .uploaded: return "SYNCED"
        }
    }

    private func badgeColor(for state: PendingAssessmentRecord.UploadState) -> Color {
        switch state {
        case .failedPermanent: return .red
        case .pendingUpload, .localSaved, .recording, .failedRetryable: return .orange
        case .uploading: return .blue
        case .uploaded: return .green
        }
    }
}
