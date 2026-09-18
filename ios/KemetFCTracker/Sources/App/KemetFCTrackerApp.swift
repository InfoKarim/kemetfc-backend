//
//  KemetFCTrackerApp.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  App entry point. Set KEMET_BACKEND_BASE_URL to the deployed backend
//  (https://app.kemetfc.com in production, or the LAN address of a
//  local `uvicorn main:app` for on-field testing against a laptop).
//

import SwiftUI

@main
struct KemetFCTrackerApp: App {
    @StateObject private var session: AssessmentSessionViewModel
    @Environment(\.scenePhase) private var scenePhase

    init() {
        let baseURL = URL(string: ProcessInfo.processInfo.environment["KEMET_BACKEND_BASE_URL"] ?? "https://app.kemetfc.com")!
        let apiClient = KemetAPIClient(configuration: .init(baseURL: baseURL))
        _session = StateObject(wrappedValue: AssessmentSessionViewModel(apiClient: apiClient))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .task { await session.start() }
        }
        // Spec Phase 5: "Retry pending uploads ... when app returns to
        // foreground." session.start() already retries once on launch;
        // this covers every later foreground transition too.
        .onChange(of: scenePhase) { _, newPhase in
            if newPhase == .active {
                Task { await session.handleAppDidBecomeActive() }
            }
        }
    }
}

/// Root screen wiring PlayerSelectionView -> AssessmentCameraView per the
/// workflow in spec section 24, with the reliability fix's states
/// layered on top (spec Phases 1/2/4/5): a just-finished recording
/// always shows its summary FIRST regardless of upload outcome (Phase
/// 5), and a recording that started without a reachable backend keeps
/// running with its own lightweight screen rather than falling back to
/// player selection (Phase 4) — cameraCaptureManager is already
/// recording in that state, this is presentation only.
struct RootView: View {
    @EnvironmentObject var session: AssessmentSessionViewModel
    @State private var showPendingUploads = false

    var body: some View {
        Group {
            if let recordingId = session.lastFinishedRecordingId, session.pendingStore.record(id: recordingId) != nil {
                AssessmentSummaryView(
                    store: session.pendingStore,
                    recordId: recordingId,
                    onRetryUpload: {
                        Task { await session.retryUpload(recordId: recordingId) }
                    },
                    onDone: { session.clearFinishedRecording() },
                    onViewPendingUploads: { showPendingUploads = true }
                )
            } else if session.confirmedPlayer == nil {
                PlayerSelectionView(
                    capture: session.cameraCaptureManager,
                    onPlayerConfirmed: { session.confirmPlayer($0) },
                    searchPlayers: { _ in [] }, // wire to GET /players?search=... before shipping
                    resolveCheckInToken: { token in await session.resolveCheckInToken(token) }
                )
                .safeAreaInset(edge: .bottom) {
                    // actionablePendingCount excludes FAILED_PERMANENT
                    // (including old XCTest-contamination entries still
                    // on this device) and UPLOADED — only counts what
                    // will actually still happen automatically.
                    if session.pendingStore.actionablePendingCount > 0 {
                        Button {
                            showPendingUploads = true
                        } label: {
                            Label("\(session.pendingStore.actionablePendingCount) Pending Upload(s)", systemImage: "icloud.and.arrow.up")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .padding()
                    }
                }
            } else if let coordinator = session.trackingCoordinator, let player = session.confirmedPlayer {
                // The REAL coordinator AssessmentSessionViewModel created in
                // startAssessment(...) and is actively feeding camera frames
                // to — never a fresh throwaway instance the view constructs
                // itself, which would silently desync the HUD from what's
                // actually being tracked.
                AssessmentCameraView(
                    capture: session.cameraCaptureManager,
                    dockKit: session.dockKitManager,
                    coordinator: coordinator,
                    player: player,
                    backendDiagnostics: session.backendDiagnostics,
                    onTapToLock: { point in
                        // Phase 2 fix: this was a literal no-op — tap-to-lock
                        // was completely disconnected end to end, not merely
                        // hardcoded to frame-center. AssessmentCameraView has
                        // already converted the raw view tap into a
                        // normalized camera-space point; the coordinator
                        // supplies the matching detections/pixel buffer from
                        // that same moment (see TrackingCoordinator
                        // .latestDetections/.latestPixelBuffer) so the tap
                        // handler never needs its own stale copy of either.
                        guard let pixelBuffer = coordinator.latestPixelBuffer else { return }
                        session.handlePlayerTap(point, detections: coordinator.latestDetections, pixelBuffer: pixelBuffer)
                    },
                    onStartAssessment: {
                        Task { await session.startAssessment(mode: .smartSoccer, speedProfile: .sport) }
                    },
                    onStopAssessment: {
                        Task { await session.stopAssessment() }
                    },
                    onConfirmPlayerTracked: { answer in
                        Task { await session.confirmPlayerTracked(answer) }
                    }
                )
            } else if session.isAssessmentActive {
                // Recording started (spec Phase 1: unconditionally, the
                // instant Start Assessment was tapped) but the backend
                // tracking session isn't wired up yet — either still
                // retrying in the background, or genuinely offline for
                // this whole recording. The video is safely recording
                // via cameraCaptureManager regardless; this is presentation
                // only, and Stop Assessment here is the SAME call as the
                // full camera view's (spec Phase 4: never forces a
                // restart or re-scan just because live tracking hasn't
                // connected yet).
                recordingWithoutTrackingScreen
            } else {
                // Player confirmed, but startAssessment(...) hasn't run
                // yet — a real app shows a "Connect Flow 2 Pro / press
                // Start Assessment" screen here; kept minimal since that
                // screen's design is a product decision, not a
                // tracking-engineering one. This is also the natural
                // place to reach Gimbal Diagnostics / DockKit Test Mode
                // exactly when a coach would want to sanity-check the
                // gimbal BEFORE trusting it during a real assessment.
                NavigationStack {
                    VStack(spacing: 16) {
                        Text("Player confirmed. Connect the Flow 2 Pro, then start the assessment.")
                            .multilineTextAlignment(.center)
                            .padding()
                        Button("Start Assessment") {
                            Task { await session.startAssessment(mode: .smartSoccer, speedProfile: .sport) }
                        }
                        .buttonStyle(.borderedProminent)

                        NavigationLink("Gimbal Diagnostics") {
                            GimbalDiagnosticsView(dockKit: session.dockKitManager)
                        }
                        NavigationLink("DockKit Test Mode") {
                            DockKitTestModeView(dockKit: session.dockKitManager, capture: session.cameraCaptureManager)
                        }
                        if session.pendingStore.actionablePendingCount > 0 {
                            Button("Pending Uploads (\(session.pendingStore.actionablePendingCount))") {
                                showPendingUploads = true
                            }
                        }
                    }
                }
            }
        }
        .sheet(isPresented: $showPendingUploads) {
            PendingUploadsView(
                store: session.pendingStore,
                onRetry: { recordId in Task { await session.retryUpload(recordId: recordId) } }
            )
        }
    }

    private var recordingWithoutTrackingScreen: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 20) {
                Spacer()
                Image(systemName: "wifi.slash")
                    .font(.system(size: 40))
                    .foregroundStyle(.orange)
                Text("Recording...")
                    .font(.title2).bold()
                    .foregroundStyle(.white)
                Text("Saving locally. Live tracking will connect automatically once the KEMET server is reachable.")
                    .font(.subheadline)
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
                Spacer()
                Button("Stop Assessment") {
                    Task { await session.stopAssessment() }
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .padding(.bottom, 40)
            }
        }
    }
}
