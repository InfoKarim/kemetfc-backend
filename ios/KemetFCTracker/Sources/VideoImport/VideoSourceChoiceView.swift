//
//  VideoSourceChoiceView.swift
//  KemetFCTracker
//
//  PLAYER ASSESSMENT, after the player is chosen: where does the video come from?
//
//    RECORD VIDEO  - hands over to the existing camera flow, unchanged
//    UPLOAD VIDEO  - the coach picks an existing video from Photos or Files; it is copied into durable
//                    storage, checked, and shown for review (VideoImportService)
//
//  The player was chosen BEFORE this screen and is the player the video is saved against. This screen never
//  starts a recording, never opens the camera and never uploads: it only imports a file the coach picks.
//

import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct VideoSourceChoiceView: View {
    @ObservedObject var mode: VideoFirstAssessmentMode
    let store: PendingAssessmentStore
    let player: KemetPlayerSummary
    let onChangePlayer: () -> Void

    @State private var showSourceDialog = false
    @State private var showPhotoPicker = false
    @State private var showFilePicker = false
    @State private var photoItem: PhotosPickerItem?
    @State private var importTask: Task<Void, Never>?
    @State private var isImporting = false
    @State private var message: String?

    private var service: VideoImportService { VideoImportService(store: store) }

    var body: some View {
        ZStack {
            Color.kemetNavy.ignoresSafeArea()
            VStack(spacing: 24) {
                VStack(spacing: 6) {
                    Text("PLAYER ASSESSMENT").font(.caption).bold().foregroundStyle(.white.opacity(0.7))
                    Text(player.fullName).font(.title2).bold().foregroundStyle(.white)
                    Text("Choose the video source").font(.subheadline).foregroundStyle(.white.opacity(0.7))
                }
                .padding(.top, 48)

                VStack(spacing: 14) {
                    Button { mode.chooseRecording() } label: {
                        sourceLabel(icon: "video.fill", title: "RECORD VIDEO", detail: "Camera and player tracking")
                    }
                    .buttonStyle(.plain)
                    .background(Color.kemetGold, in: RoundedRectangle(cornerRadius: 14))
                    .foregroundStyle(Color.kemetNavy)
                    .disabled(isImporting)

                    Button { showSourceDialog = true } label: {
                        sourceLabel(icon: "square.and.arrow.up", title: "UPLOAD VIDEO", detail: "A video from Photos or Files")
                    }
                    .buttonStyle(.plain)
                    .background(.white.opacity(0.12), in: RoundedRectangle(cornerRadius: 14))
                    .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color.kemetGold, lineWidth: 2))
                    .foregroundStyle(.white)
                    .disabled(isImporting)
                }
                .padding(.horizontal, 24)

                if isImporting {
                    VStack(spacing: 10) {
                        ProgressView().tint(.white)
                        Text("Preparing the video…").foregroundStyle(.white)
                        Text("Copying it onto this iPhone and checking it. Large videos can take a minute.")
                            .font(.footnote).foregroundStyle(.white.opacity(0.7)).multilineTextAlignment(.center)
                        Button("Cancel") { importTask?.cancel() }
                            .buttonStyle(.bordered).tint(.white)
                    }
                    .padding(.horizontal, 32)
                } else if let message {
                    Text(message).font(.footnote).foregroundStyle(.orange)
                        .multilineTextAlignment(.center).padding(.horizontal, 32)
                }

                Spacer()

                Button("Change player", action: onChangePlayer)
                    .foregroundStyle(.white.opacity(0.8))
                    .disabled(isImporting)
                    .padding(.bottom, 32)
            }
        }
        .confirmationDialog("Upload video from", isPresented: $showSourceDialog, titleVisibility: .visible) {
            Button("Photo Library") { showPhotoPicker = true }
            Button("Files") { showFilePicker = true }
            Button("Cancel", role: .cancel) {}
        }
        // `.current` asks Photos for the video as it is stored, not a re-encoded "compatible" copy.
        .photosPicker(isPresented: $showPhotoPicker, selection: $photoItem, matching: .videos, preferredItemEncoding: .current)
        .onChange(of: photoItem) { _, item in
            guard let item else { return }
            photoItem = nil
            beginImport {
                guard let picked = try await item.loadTransferable(type: PickedVideoFile.self) else {
                    throw VideoImportError.copyFailed("Photos did not return a video file.")
                }
                return picked.stagedURL
            }
        }
        .fileImporter(isPresented: $showFilePicker, allowedContentTypes: [.movie, .video, .quickTimeMovie, .mpeg4Movie], allowsMultipleSelection: false) { result in
            switch result {
            case .failure(let error):
                message = "Could not open that file. \(error.localizedDescription)"
            case .success(let urls):
                guard let url = urls.first else { return }
                beginImport {
                    let scoped = url.startAccessingSecurityScopedResource()
                    defer { if scoped { url.stopAccessingSecurityScopedResource() } }
                    return try await Task.detached { try VideoImportService.stage(copyOf: url, coordinated: true) }.value
                }
            }
        }
    }

    private func sourceLabel(icon: String, title: String, detail: String) -> some View {
        VStack(spacing: 2) {
            HStack { Image(systemName: icon); Text(title).bold() }
            Text(detail).font(.caption)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 18)
        .contentShape(RoundedRectangle(cornerRadius: 14))
    }

    /// Runs one import. `stage` brings the picked video into the staging folder and returns the copy; the rest
    /// (move into durable storage, hash, measure, check, write the record) is VideoImportService.
    private func beginImport(stage: @escaping () async throws -> URL) {
        guard !isImporting else { return }
        message = nil
        isImporting = true
        VideoImportService.cleanStaging()
        importTask = Task {
            defer {
                isImporting = false
                importTask = nil
            }
            var staged: URL?
            do {
                staged = try await stage()
                try Task.checkCancellation()
                let record = try await service.importStagedVideo(at: staged!, player: player)
                staged = nil
                mode.importCompleted(recordId: record.id)
            } catch is CancellationError {
                if let staged { try? FileManager.default.removeItem(at: staged) }
                message = "Import cancelled."
            } catch {
                if let staged { try? FileManager.default.removeItem(at: staged) }
                message = (error as? LocalizedError)?.errorDescription ?? "The video could not be imported. \(error.localizedDescription)"
            }
        }
    }
}
