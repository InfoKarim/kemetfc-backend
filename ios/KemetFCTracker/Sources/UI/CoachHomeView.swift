//
//  CoachHomeView.swift
//  KemetFCTracker
//
//  The coach's landing screen — shown after login, before player
//  selection. Deliberately narrower than the web dashboard's own
//  /dashboard (search across players/teams/videos, drill-library
//  recommendations, etc.): this app's only real job is recording an
//  assessment, so the only real data surfaced here is what the app
//  itself actually knows — the pending-uploads count from
//  PendingAssessmentStore — never a fabricated "development score" or
//  activity feed the app has no way to compute.
//

import SwiftUI

struct CoachHomeView: View {
    let currentUsername: String?
    let avatarURL: URL?
    let onUploadAvatar: (Data) async -> String?
    let onRemoveAvatar: () async -> Void
    let pendingCount: Int
    let webDashboardBaseURL: URL
    let onStartAssessment: () -> Void
    let onViewPendingUploads: () -> Void
    let onSignOut: () async -> Void

    @State private var isSigningOut = false
    @State private var showWebDashboard = false

    var body: some View {
        ZStack {
            Color.kemetNavy.ignoresSafeArea()

            VStack(spacing: 24) {
                header

                Spacer()

                VStack(spacing: 8) {
                    Text("Ready to run an assessment?")
                        .font(.title2.bold())
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.center)
                    Text("Record a player and KEMET FC will handle tracking and upload automatically.")
                        .font(.subheadline)
                        .foregroundStyle(.white.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
                .padding(.horizontal, 28)

                VStack(spacing: 12) {
                    Button(action: onStartAssessment) {
                        Text("Start Assessment")
                            .bold()
                            .frame(maxWidth: .infinity)
                            .padding()
                            .background(Color.kemetGold)
                            .foregroundStyle(Color.kemetNavy)
                            .clipShape(RoundedRectangle(cornerRadius: 14))
                    }

                    Button(action: onViewPendingUploads) {
                        HStack {
                            Image(systemName: "icloud.and.arrow.up")
                            Text(pendingCount > 0 ? "\(pendingCount) Pending Upload(s)" : "Pending Assessments")
                        }
                        .font(.subheadline.bold())
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(.white.opacity(0.08))
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }

                    // The real web dashboard (Players, Teams, Assessments,
                    // Training Plans, Drill Library, Videos, Matches,
                    // Reports, Calendar, Messaging, Payment) embedded via
                    // WKWebView — see WebDashboardScreen's header comment
                    // for why this isn't reimplemented natively.
                    Button {
                        showWebDashboard = true
                    } label: {
                        HStack {
                            Image(systemName: "square.grid.2x2")
                            Text("Open Full Dashboard")
                        }
                        .font(.subheadline.bold())
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(.white.opacity(0.08))
                        .foregroundStyle(.white)
                        .clipShape(RoundedRectangle(cornerRadius: 14))
                    }
                }
                .padding(.horizontal, 28)

                Spacer()
                Spacer()
            }
        }
        .fullScreenCover(isPresented: $showWebDashboard) {
            WebDashboardScreen(baseURL: webDashboardBaseURL, onClose: { showWebDashboard = false })
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("KEMET FC").font(.headline.bold()).foregroundStyle(.white)
                Text("COACH")
                    .font(.caption2.bold())
                    .foregroundStyle(.white.opacity(0.6))
            }
            Spacer()
            AccountAvatarControl(
                username: currentUsername,
                avatarURL: avatarURL,
                onUpload: onUploadAvatar,
                onRemove: onRemoveAvatar
            )
            Button {
                isSigningOut = true
                Task {
                    await onSignOut()
                    isSigningOut = false
                }
            } label: {
                Text(isSigningOut ? "Signing out..." : "Sign out")
                    .font(.caption.bold())
            }
            .buttonStyle(.bordered)
            .tint(.white)
            .disabled(isSigningOut)
        }
        .padding()
    }

}
