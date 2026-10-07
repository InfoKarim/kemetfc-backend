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
    @State private var coachWantsToRecord = false
    /// Set when the coach came in through PLAYER ASSESSMENT: after the player is chosen they pick RECORD VIDEO
    /// (the existing camera flow below) or UPLOAD VIDEO (an imported video, same pending record and upload).
    @ObservedObject private var videoFirst = VideoFirstAssessmentMode.shared
    /// The saved video whose player the coach is choosing (SELECT PLAYER IN VIDEO).
    @State private var selectingTargetRecordId: String?

    var body: some View {
        Group {
            if session.isCheckingSession {
                ZStack {
                    Color.kemetNavy.ignoresSafeArea()
                    ProgressView().tint(.white)
                }
            } else if !session.isAuthenticated {
                LoginView(onLogin: { username, password in
                    await session.login(username: username, password: password)
                })
            } else if session.userRole == "guardian" {
                GuardianHomeView(
                    viewModel: GuardianViewModel(apiClient: session.sharedAPIClient),
                    onSignOut: { await session.logout() },
                    currentUsername: session.authenticatedUsername,
                    avatarURL: session.avatarURL,
                    onUploadAvatar: { data in await session.uploadAvatar(imageData: data) },
                    onRemoveAvatar: { await session.removeAvatar() }
                )
            } else if let targetId = selectingTargetRecordId, session.pendingStore.record(id: targetId) != nil {
                // The coach shows the AI which person in a saved video is the player. Nothing starts from here.
                SelectPlayerInVideoView(
                    store: session.pendingStore,
                    recordId: targetId,
                    onClose: { selectingTargetRecordId = nil }
                )
            } else if let recordingId = session.lastFinishedRecordingId, session.pendingStore.record(id: recordingId) != nil {
                AssessmentSummaryView(
                    store: session.pendingStore,
                    recordId: recordingId,
                    onRetryUpload: {
                        Task { await session.retryUpload(recordId: recordingId) }
                    },
                    onDone: {
                        session.clearFinishedRecording()
                        coachWantsToRecord = false
                    },
                    onViewPendingUploads: { showPendingUploads = true },
                    onConfirmPlayerTracked: { answer in
                        Task { await session.confirmPlayerTracked(answer) }
                    },
                    onSelectPlayerInVideo: { selectingTargetRecordId = recordingId }
                )
            } else if videoFirst.isActive, let importedId = videoFirst.importedRecordId,
                      session.pendingStore.record(id: importedId) != nil {
                // An uploaded video, imported and checked, waiting for SAVE ASSESSMENT (or already saved).
                // It is the same kind of pending record a recording makes; saving hands it to the same
                // upload code a recording uses.
                let importer = VideoImportService(store: session.pendingStore)
                NavigationStack {
                    ImportedVideoReviewView(
                        store: session.pendingStore,
                        mode: videoFirst,
                        recordId: importedId,
                        onSave: {
                            guard importer.markSaved(recordId: importedId) else { return }
                            videoFirst.save(importedId)
                            Task { await session.retryUpload(recordId: importedId) }
                        },
                        onCancel: {
                            importer.discardUnsaved(recordId: importedId)
                            videoFirst.returnToSourceChoice()
                        },
                        onRetryUpload: { Task { await session.retryUpload(recordId: importedId) } },
                        onDone: {
                            videoFirst.end()
                            session.clearFinishedRecording()
                            coachWantsToRecord = false
                        },
                        onViewPendingUploads: { showPendingUploads = true },
                        onSelectPlayerInVideo: { selectingTargetRecordId = importedId }
                    )
                }
            } else if !coachWantsToRecord {
                CoachHomeView(
                    currentUsername: session.authenticatedUsername,
                    avatarURL: session.avatarURL,
                    onUploadAvatar: { data in await session.uploadAvatar(imageData: data) },
                    onRemoveAvatar: { await session.removeAvatar() },
                    pendingCount: session.pendingStore.actionablePendingCount,
                    webDashboardBaseURL: session.sharedAPIClient.baseURL,
                    onStartAssessment: { coachWantsToRecord = true },
                    onViewPendingUploads: { showPendingUploads = true },
                    onSignOut: { await session.logout() }
                )
            } else if session.confirmedPlayer == nil {
                PlayerSelectionView(
                    onPlayerConfirmed: { session.confirmPlayer($0) },
                    searchPlayers: { query in await session.searchPlayers(query) },
                    findByJerseyNumber: { number in await session.findByJerseyNumber(number) },
                    onSignOut: { await session.logout() },
                    currentUsername: session.authenticatedUsername,
                    avatarURL: session.avatarURL,
                    onUploadAvatar: { data in await session.uploadAvatar(imageData: data) },
                    onRemoveAvatar: { await session.removeAvatar() },
                    onBack: { coachWantsToRecord = false }
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
                                .bold()
                                .frame(maxWidth: .infinity)
                                .padding()
                                .background(Color.kemetGold)
                                .foregroundStyle(Color.kemetNavy)
                                .clipShape(Capsule())
                        }
                        .padding()
                        .background(Color.kemetNavy)
                    }
                }
            } else if let player = session.confirmedPlayer, videoFirst.isActive, videoFirst.sourceChoice == nil {
                VideoSourceChoiceView(
                    mode: videoFirst,
                    store: session.pendingStore,
                    player: player,
                    // Nothing is recording at this point, so clearing the chosen player is safe.
                    onChangePlayer: { session.clearFinishedRecording() }
                )
            } else if let player = session.confirmedPlayer {
                // ROOT-CAUSE FIX (physical-device investigation, 2026-09-27): the camera
                // preview used to live INSIDE AssessmentCameraView, which only enters the
                // view tree once `trackingCoordinator` becomes non-nil — i.e. AFTER
                // `AVCaptureMovieFileOutput` is already recording (set inside wireTracking(),
                // itself called from startAssessment() after the movie file output is
                // already confirmed recording). That meant `CameraPreviewRepresentable
                // .makeUIView` created a BRAND NEW `AVCaptureVideoPreviewLayer` and attached
                // it to the live session — a genuinely expensive, ~200-250ms synchronous
                // main-thread AVFoundation operation — WHILE the movie writer was already
                // active. Four independent physical-device runs, each fully instrumented,
                // showed `AVFoundationErrorDomain -11818`/`NSOSStatusErrorDomain -16414`
                // arriving 10-30ms after that exact statement returned, every single time —
                // with no AVCaptureSessionRuntimeError/WasInterrupted notification, no
                // explicit stopRecording/stopRunning call, no session/device/connection
                // mutation, and no other production-only code path anywhere in the
                // recording window. Every other candidate (fps, session preset, codec,
                // stabilization, backend networking, TelemetryUploader, the entire
                // TrackingCoordinator/Vision/CIContext object graph under real load, and the
                // real CameraCaptureManager/AVCaptureVideoDataOutput/AVCaptureMovieFileOutput
                // in isolation) was independently proven safe.
                //
                // THE FIX: mount exactly ONE `CameraPreviewRepresentable` — hence exactly one
                // `AVCaptureVideoPreviewLayer` — for the entire lifetime of a confirmed
                // player, BEFORE `Start Assessment` is ever tapped and therefore before
                // `movieFileOutput.startRecording` is ever called. SwiftUI preserves a
                // `UIViewRepresentable`'s backing UIView/CALayer across body re-evaluations
                // as long as it stays at the same position in the view tree — it does NOT
                // get destroyed and recreated when `trackingCoordinator`/`isAssessmentActive`
                // change below, so the preview layer's one-time expensive attach happens
                // once, safely, with no movie file output active yet. The per-state content
                // (connect-gimbal prompt, "recording without tracking" banner, or the full
                // AssessmentCameraView HUD/overlay) is layered ON TOP of that persistent
                // preview instead of each owning/recreating its own.
                ZStack {
                    CameraPreviewRepresentable(capture: session.cameraCaptureManager)
                        .ignoresSafeArea()

                    if let coordinator = session.trackingCoordinator {
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
                        // The camera preview behind this content is the SAME
                        // persistent layer the assessment itself will use — see
                        // the fix note above this ZStack.
                        NavigationStack {
                            VStack(spacing: 16) {
                                Text("Player confirmed. Connect the Flow 2 Pro, then start the assessment.")
                                    .multilineTextAlignment(.center)
                                    .padding()
                                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
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
                        .toolbarBackground(.hidden, for: .navigationBar)
                    }
                }
            }
        }
        .sheet(isPresented: $showPendingUploads) {
            PendingUploadsView(
                store: session.pendingStore,
                onRetry: { recordId in Task { await session.retryUpload(recordId: recordId) } },
                onDelete: { recordId in session.pendingStore.deleteRecord(id: recordId) }
            )
        }
    }

    private var recordingWithoutTrackingScreen: some View {
        // No opaque background here anymore — this now overlays the persistent camera
        // preview mounted by the parent ZStack (see the fix note above it) rather than
        // hiding it behind a black screen, which also means the coach can genuinely see
        // that recording is live even before tracking connects.
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
        .background(.black.opacity(0.35))
    }
}
