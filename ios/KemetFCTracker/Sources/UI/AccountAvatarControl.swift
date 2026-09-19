//
//  AccountAvatarControl.swift
//  KemetFCTracker
//
//  Shared header avatar for CoachHomeView/GuardianHomeView/
//  PlayerSelectionView — matches the web dashboard's own
//  #account-avatar (white circle, bold navy initials, or the real
//  uploaded photo) and adds the SAME "tap to change / remove" capability
//  auth_client.js's ensureAccountWidget already gives every web page,
//  via the real POST/DELETE /auth/me/avatar endpoints — never a
//  decorative, non-functional badge.
//

import PhotosUI
import SwiftUI

struct AccountAvatarControl: View {
    let username: String?
    let avatarURL: URL?
    let onUpload: (Data) async -> String?
    let onRemove: () async -> Void

    @State private var photosPickerItem: PhotosPickerItem?
    @State private var showRemoveConfirm = false
    @State private var isBusy = false
    @State private var errorMessage: String?

    var body: some View {
        if let username, !username.isEmpty {
            VStack(alignment: .trailing, spacing: 2) {
                HStack(spacing: 6) {
                    PhotosPicker(selection: $photosPickerItem, matching: .images) {
                        avatarCircle
                    }
                    .disabled(isBusy)

                    if avatarURL != nil {
                        Button {
                            showRemoveConfirm = true
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.caption2)
                                .foregroundStyle(.white.opacity(0.55))
                        }
                        .disabled(isBusy)
                    }

                    Text(username)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.85))
                }

                if let errorMessage {
                    Text(errorMessage)
                        .font(.caption2)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.trailing)
                        .frame(maxWidth: 180)
                }
            }
            .onChange(of: photosPickerItem) { _, newItem in
                guard let newItem else { return }
                Task {
                    isBusy = true
                    errorMessage = nil
                    if let data = try? await newItem.loadTransferable(type: Data.self) {
                        errorMessage = await onUpload(data)
                    } else {
                        errorMessage = "Could not read the selected photo."
                    }
                    isBusy = false
                    photosPickerItem = nil
                }
            }
            .confirmationDialog(
                "Remove profile picture?",
                isPresented: $showRemoveConfirm,
                titleVisibility: .visible
            ) {
                Button("Remove Photo", role: .destructive) {
                    Task {
                        isBusy = true
                        await onRemove()
                        isBusy = false
                    }
                }
                Button("Cancel", role: .cancel) {}
            }
        }
    }

    @ViewBuilder
    private var avatarCircle: some View {
        ZStack {
            Circle().fill(.white).frame(width: 28, height: 28)

            if let avatarURL {
                AsyncImage(url: avatarURL) { image in
                    image.resizable().aspectRatio(contentMode: .fill)
                } placeholder: {
                    initialsText
                }
                .frame(width: 28, height: 28)
                .clipShape(Circle())
            } else {
                initialsText
            }

            if isBusy {
                Circle().fill(.black.opacity(0.35)).frame(width: 28, height: 28)
                ProgressView().tint(.white).scaleEffect(0.55)
            }
        }
    }

    private var initialsText: some View {
        Text(String((username ?? "").prefix(2)).uppercased())
            .font(.caption2.bold())
            .foregroundStyle(Color.kemetNavy)
    }
}
