//
//  AssessmentCameraView.swift
//  KemetFCTracker
//
//  The coach's live assessment-camera screen — redesigned as a premium,
//  production sports-camera interface (full-screen feed, floating glass
//  HUD/dock, animated corner-bracket tracking, restrained toasts) in
//  place of the earlier developer-prototype layout. This file is
//  PRESENTATION ONLY: every piece of tracking/camera/gimbal/assessment
//  logic below is read from the same real objects the old layout used
//  (CameraCaptureManager, DockKitManager, TrackingCoordinator,
//  AssessmentSessionViewModel via the closures passed in) — nothing
//  here reimplements or changes tracking behavior.
//

import AVFoundation
import SwiftUI
import UIKit

struct AssessmentCameraView: View {
    @ObservedObject var capture: CameraCaptureManager
    @ObservedObject var dockKit: DockKitManager
    @ObservedObject var coordinator: TrackingCoordinator

    let player: KemetPlayerSummary
    let backendDiagnostics: BackendDiagnostics

    let onTapToLock: (NormalizedPoint) -> Void
    let onStartAssessment: () -> Void
    let onStopAssessment: () -> Void
    let onConfirmPlayerTracked: (String) -> Void

    @Environment(\.verticalSizeClass) private var verticalSizeClass

    @State private var isFieldTestMode = false
    @State private var isRecording = false
    @State private var recordingStartTime: Date?
    @State private var elapsedSeconds = 0
    @State private var showPlayerTrackedConfirmation = false
    @State private var showSettingsSheet = false
    @State private var showGimbalDetail = false
    @State private var unavailableModeMessage: String?
    @State private var targetToast: TargetToastKind?

    private let elapsedTimer = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        GeometryReader { proxy in
            ZStack {
                CameraPreviewRepresentable(session: capture.session)
                    .ignoresSafeArea()
                    .contentShape(Rectangle())
                    .gesture(tapToLockGesture(viewSize: proxy.size))

                centerReticle
                    .allowsHitTesting(false)

                PlayerTrackingOverlay(
                    detections: coordinator.latestDetections,
                    lockedBox: coordinator.lastFramingTarget,
                    playerState: coordinator.playerState,
                    confidence: coordinator.playerConfidence,
                    playerFirstName: player.firstNameEn
                )

                if isFieldTestMode {
                    VStack {
                        Spacer()
                        HStack {
                            fieldTestOverlay
                            Spacer()
                        }
                    }
                    .padding()
                    .padding(.bottom, verticalSizeClass == .compact ? 8 : 90)
                }

                if verticalSizeClass == .compact {
                    landscapeChrome
                } else {
                    portraitChrome
                }

                if let targetToast {
                    VStack {
                        Spacer()
                        TargetLostToast(kind: targetToast)
                            .padding(.bottom, verticalSizeClass == .compact ? 90 : 170)
                        Spacer().frame(height: verticalSizeClass == .compact ? 0 : 0)
                    }
                    .transition(.opacity.combined(with: .scale(scale: 0.95)))
                    .allowsHitTesting(false)
                }

                if let unavailableModeMessage {
                    VStack {
                        Spacer()
                        Text(unavailableModeMessage)
                            .font(.footnote)
                            .foregroundStyle(.white)
                            .padding(10)
                            .background(.black.opacity(0.75), in: RoundedRectangle(cornerRadius: 10))
                            .padding(.bottom, 140)
                    }
                    .transition(.opacity)
                    .allowsHitTesting(false)
                }
            }
        }
        .background(.black)
        .statusBarHidden(verticalSizeClass == .compact)
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
        .sheet(isPresented: $showSettingsSheet) {
            AssessmentSettingsSheet(
                isFieldTestMode: $isFieldTestMode,
                isPaused: coordinator.isPaused,
                onPauseToggle: {
                    coordinator.isPaused ? coordinator.resumeTracking() : coordinator.pauseTracking()
                },
                onCenterGimbal: coordinator.centerGimbal
            )
        }
        .sheet(isPresented: $showGimbalDetail) {
            GimbalDetailSheet(
                state: dockKit.connectionState,
                batteryLevel: dockKit.batteryLevel,
                lastAccessoryName: dockKit.lastAccessoryName
            )
        }
        .onReceive(elapsedTimer) { _ in
            guard isRecording, let recordingStartTime else { return }
            elapsedSeconds = Int(Date().timeIntervalSince(recordingStartTime))
        }
        .onChange(of: coordinator.playerState) { old, new in
            handlePlayerStateChange(from: old, to: new)
        }
    }

    // MARK: - Chrome layouts

    private var portraitChrome: some View {
        VStack {
            hud.padding(.horizontal, 14).padding(.top, 8)
            if let warning = coordinator.thermalManager.userFacingWarning {
                thermalWarningStrip(warning)
            }
            Spacer()
            dock.padding(.horizontal, 20).padding(.bottom, 16)
        }
    }

    private var landscapeChrome: some View {
        VStack {
            HStack(alignment: .top) {
                hud
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.top, 6)
            if let warning = coordinator.thermalManager.userFacingWarning {
                HStack {
                    thermalWarningStrip(warning)
                    Spacer()
                }
            }
            Spacer()
            HStack {
                Spacer()
                dock
                Spacer()
            }
            .padding(.bottom, 10)
        }
    }

    private var hud: some View {
        AssessmentHUD(
            player: player,
            isRecording: isRecording,
            elapsedSeconds: elapsedSeconds,
            playerState: coordinator.playerState,
            gimbalState: dockKit.connectionState,
            ballModelAvailable: coordinator.modelVersionInfo.ballModelStatus != "missing"
        )
        .overlay(alignment: .topTrailing) {
            ConnectionStatusView(
                state: dockKit.connectionState,
                batteryLevel: dockKit.batteryLevel,
                onTap: { showGimbalDetail = true }
            )
            .offset(y: -34)
        }
    }

    private var dock: some View {
        AssessmentControlDock(
            mode: coordinator.mode,
            ballModelAvailable: coordinator.modelVersionInfo.ballModelStatus != "missing",
            isRecording: isRecording,
            onSelectMode: { coordinator.setMode($0) },
            onUnavailableModeSelected: {
                showTransientMessage(
                    "No ball-tracking model is installed on this device yet.",
                    binding: $unavailableModeMessage
                )
            },
            onRecordToggle: toggleRecording,
            onSelectPlayer: {
                showTransientMessage("Tap the athlete on screen to lock them as the target.", binding: $unavailableModeMessage)
            },
            onReselectPlayer: {
                showTransientMessage("Tap the athlete on screen to reselect the target.", binding: $unavailableModeMessage)
            },
            onOpenSettings: { showSettingsSheet = true }
        )
    }

    private var centerReticle: some View {
        Image(systemName: "viewfinder")
            .font(.system(size: 34, weight: .ultraLight))
            .foregroundStyle(.white.opacity(0.18))
    }

    private var fieldTestOverlay: some View {
        FieldTestOverlay(
            captureFPS: capture.effectiveFrameRate,
            inferenceFPS: coordinator.inferenceFPS,
            playerConfidence: coordinator.playerConfidence,
            trackId: coordinator.lockedTrackId,
            droppedFrameCount: coordinator.droppedFrameCount,
            gimbalState: dockKit.connectionState,
            gimbalBatteryLevel: dockKit.batteryLevel,
            ballConfidence: coordinator.ballConfidence,
            ballModelStatus: coordinator.modelVersionInfo.ballModelStatus,
            latencyMs: coordinator.lastStageTiming?.totalMs,
            thermalState: coordinator.thermalManager.thermalState,
            backendDiagnostics: backendDiagnostics,
            // Always true here: this view only ever renders once
            // RootView already has a real TrackingCoordinator (see
            // KemetFCTrackerApp.swift) — a recording without one shows
            // the separate recordingWithoutTrackingScreen instead.
            trackingAvailable: true,
            // Upload only begins after Stop Assessment — nothing to
            // show yet while still actively recording.
            currentUploadState: nil
        )
    }

    private func thermalWarningStrip(_ text: String) -> some View {
        Text(text)
            .font(.caption2)
            .bold()
            .padding(.horizontal, 10)
            .padding(.vertical, 5)
            .background(.orange.opacity(0.85), in: Capsule())
            .foregroundStyle(.white)
    }

    // MARK: - Interaction

    private func tapToLockGesture(viewSize: CGSize) -> some Gesture {
        SpatialTapGesture()
            .onEnded { value in
                guard let cameraPoint = TapToLockConverter.normalizedCameraPoint(
                    tapPoint: value.location,
                    viewSize: viewSize,
                    videoDimensions: capture.activeVideoDimensions
                ) else { return }
                HapticFeedback.impact(.medium)
                onTapToLock(cameraPoint)
            }
    }

    private func toggleRecording() {
        let wasRecording = isRecording
        isRecording.toggle()
        if wasRecording {
            HapticFeedback.notify(.warning)
            onStopAssessment()
            recordingStartTime = nil
            showPlayerTrackedConfirmation = true
        } else {
            HapticFeedback.impact(.rigid)
            recordingStartTime = Date()
            elapsedSeconds = 0
            onStartAssessment()
        }
    }

    private func handlePlayerStateChange(from old: PlayerTrackState, to new: PlayerTrackState) {
        let wasTracking = old == .locked || old == .reacquired
        let isTracking = new == .locked || new == .reacquired

        if wasTracking, !isTracking {
            // The tracker itself lost the athlete (no new tap involved).
            HapticFeedback.notify(.warning)
            showTransientToast(.lost)
        } else if new == .locked, old == .lost {
            // A fresh manual lock via tap — covered by the persistent
            // "PLAYER LOCKED" overlay label, not a one-off toast.
            HapticFeedback.notify(.success)
        } else if !wasTracking, isTracking {
            // Automatic recovery from temporarilyLost/searching, with no
            // new tap from the coach — this IS the "reacquired" case.
            HapticFeedback.notify(.success)
            showTransientToast(.reacquired)
        }
    }

    private func showTransientToast(_ kind: TargetToastKind) {
        withAnimation { targetToast = kind }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            withAnimation { targetToast = nil }
        }
    }

    private func showTransientMessage(_ message: String, binding: Binding<String?>) {
        withAnimation { binding.wrappedValue = message }
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
            withAnimation { binding.wrappedValue = nil }
        }
    }
}

/// Thin wrapper over UIKit's feedback generators (spec section 12) —
/// restrained, one-shot use only, never a repeating/decorative effect.
enum HapticFeedback {
    static func impact(_ style: UIImpactFeedbackGenerator.FeedbackStyle) {
        UIImpactFeedbackGenerator(style: style).impactOccurred()
    }

    static func notify(_ type: UINotificationFeedbackGenerator.FeedbackType) {
        UINotificationFeedbackGenerator().notificationOccurred(type)
    }
}

/// UIViewRepresentable wrapping AVCaptureVideoPreviewLayer — standard
/// AVFoundation preview boilerplate.
struct CameraPreviewRepresentable: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.videoPreviewLayer.session = session
        view.videoPreviewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var videoPreviewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }
}
