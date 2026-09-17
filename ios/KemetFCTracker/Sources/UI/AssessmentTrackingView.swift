//
//  AssessmentTrackingView.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  The coach's live tracking screen (spec sections 6, 17, 25): camera
//  preview, tap-to-lock overlay, status labels, mode picker, and manual
//  overrides. Deliberately non-technical by default — confidence values
//  only appear when Field Test Mode is on (spec section 32/17).
//

import AVFoundation
import SwiftUI

public struct AssessmentTrackingView: View {
    @ObservedObject var capture: CameraCaptureManager
    @ObservedObject var dockKit: DockKitManager
    @ObservedObject var coordinator: TrackingCoordinator

    @State private var candidateDetections: [PersonDetection] = []
    @State private var isFieldTestMode = false
    @State private var isRecording = false
    @State private var showPlayerTrackedConfirmation = false

    let onTapToLock: (NormalizedPoint) -> Void
    let onStartAssessment: () -> Void
    let onStopAssessment: () -> Void
    /// Coach review workflow (spec section 16) — "Correct Player
    /// Tracked: YES/NO/UNSURE", posted to the existing, tested backend
    /// endpoint via AssessmentSessionViewModel.confirmPlayerTracked(_:).
    let onConfirmPlayerTracked: (String) -> Void

    public init(
        capture: CameraCaptureManager,
        dockKit: DockKitManager,
        coordinator: TrackingCoordinator,
        onTapToLock: @escaping (NormalizedPoint) -> Void,
        onStartAssessment: @escaping () -> Void,
        onStopAssessment: @escaping () -> Void,
        onConfirmPlayerTracked: @escaping (String) -> Void
    ) {
        self.capture = capture
        self.dockKit = dockKit
        self.coordinator = coordinator
        self.onTapToLock = onTapToLock
        self.onStartAssessment = onStartAssessment
        self.onStopAssessment = onStopAssessment
        self.onConfirmPlayerTracked = onConfirmPlayerTracked
    }

    public var body: some View {
        GeometryReader { proxy in
            ZStack {
                CameraPreviewRepresentable(session: capture.session)
                    .ignoresSafeArea()
                    .overlay(trackingOverlay)
                    .contentShape(Rectangle())
                    .gesture(
                        // Phase 2 fix: this previously hardcoded every
                        // tap to frame-center (`NormalizedPoint(x: 0.5,
                        // y: 0.5)`), and the view/viewModel wiring for
                        // this callback was a literal no-op (`{ _ in }`
                        // in KemetFCTrackerApp.swift) — tap-to-lock did
                        // not exist end to end. SpatialTapGesture (not
                        // plain onTapGesture, which provides no
                        // location) gives the real view-space point;
                        // TapToLockConverter.normalizedCameraPoint
                        // inverts the preview layer's aspect-fill crop
                        // using the view's ACTUAL size (from this
                        // GeometryReader) and the capture manager's
                        // negotiated format (never a guessed 1920x1080).
                        SpatialTapGesture()
                            .onEnded { value in
                                guard let cameraPoint = TapToLockConverter.normalizedCameraPoint(
                                    tapPoint: value.location,
                                    viewSize: proxy.size,
                                    videoDimensions: capture.activeVideoDimensions
                                ) else { return }
                                onTapToLock(cameraPoint)
                            }
                    )

                VStack {
                    topStatusBar
                    if let warning = coordinator.thermalManager.userFacingWarning {
                        Text(warning)
                            .font(.caption2)
                            .bold()
                            .padding(6)
                            .frame(maxWidth: .infinity)
                            .background(.orange.opacity(0.85))
                            .foregroundStyle(.white)
                    }
                    Spacer()
                    if isFieldTestMode { fieldTestOverlay }
                    bottomControls
                }
                .padding()
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
    }

    // MARK: - Overlays

    private var trackingOverlay: some View {
        GeometryReader { proxy in
            ZStack {
                if let playerBox = coordinator.lastFramingTarget, coordinator.playerState != .lost {
                    boxOverlay(playerBox, in: proxy.size, color: .green, label: "PLAYER LOCKED")
                }
            }
        }
    }

    private func boxOverlay(_ box: NormalizedRect, in size: CGSize, color: Color, label: String) -> some View {
        let rect = CGRect(
            x: box.x * size.width, y: box.y * size.height,
            width: box.width * size.width, height: box.height * size.height
        )
        return ZStack(alignment: .topLeading) {
            Rectangle().stroke(color, lineWidth: 3).frame(width: rect.width, height: rect.height)
            Text(label).font(.caption2).bold().padding(4).background(color).foregroundStyle(.white)
        }
        .position(x: rect.midX, y: rect.midY)
    }

    // MARK: - Status bar

    private var topStatusBar: some View {
        HStack {
            gimbalStatusBadge
            ballModelStatusBadge
            Spacer()
            if isRecording {
                Label("REC", systemImage: "circle.fill").foregroundStyle(.red).bold()
            }
        }
    }

    /// Honest, always-visible exposure of what ball detection actually
    /// is right now (spec: "The app should visibly expose: BALL MODEL
    /// NOT INSTALLED" — never silently imply a trained model exists just
    /// because ball tracking runs at all). Read once from the
    /// coordinator's ballDetector, which does not change for the
    /// lifetime of a session.
    @ViewBuilder
    private var ballModelStatusBadge: some View {
        let status = coordinator.modelVersionInfo.ballModelStatus
        switch status {
        case "installed":
            EmptyView() // A real trained model is active — no caveat needed.
        case "fallback_classical":
            statusBadge("BALL: EXPERIMENTAL (NO TRAINED MODEL)", color: .orange)
        default: // "missing"
            statusBadge("BALL MODEL NOT INSTALLED", color: .red)
        }
    }

    private func statusBadge(_ text: String, color: Color) -> some View {
        Text(text).font(.caption2).bold().padding(6).background(color.opacity(0.85)).foregroundStyle(.white).clipShape(Capsule())
    }

    private var gimbalStatusBadge: some View {
        let (text, color): (String, Color) = {
            switch dockKit.connectionState {
            case .disconnected: return ("GIMBAL NOT FOUND", .gray)
            case .connected: return ("GIMBAL CONNECTED", .blue)
            case .trackingActive: return ("TRACKING ACTIVE", .green)
            case .trackingLost: return ("TRACKING LOST", .orange)
            case .reconnecting: return ("RECONNECTING", .yellow)
            }
        }()
        return Text(text).font(.caption).bold().padding(8).background(color.opacity(0.85)).foregroundStyle(.white).clipShape(Capsule())
    }

    // MARK: - Player-lost banner

    @ViewBuilder
    private var playerLostBanner: some View {
        if coordinator.playerState == .lost {
            Text("PLAYER LOST — TAP TO RESELECT")
                .bold()
                .padding()
                .frame(maxWidth: .infinity)
                .background(.red.opacity(0.85))
                .foregroundStyle(.white)
        }
    }

    /// Gimbal failsafe banner — recording is UNAFFECTED (CameraCapture
    /// Manager is entirely separate from DockKit), only gimbal motion
    /// has stopped. Resume is an explicit coach tap, never automatic,
    /// even once the accessory reconnects on its own (spec: gimbal
    /// failure must never silently resume).
    @ViewBuilder
    private var gimbalFailsafeBanner: some View {
        if coordinator.isGimbalControlLost {
            VStack(spacing: 8) {
                Text("GIMBAL CONTROL LOST — RECORDING CONTINUES").bold()
                Button("Resume Gimbal Control") { coordinator.resumeGimbalControl() }
                    .buttonStyle(.borderedProminent)
            }
            .padding()
            .frame(maxWidth: .infinity)
            .background(.orange.opacity(0.9))
            .foregroundStyle(.white)
        }
    }

    // MARK: - Bottom controls

    private var bottomControls: some View {
        VStack(spacing: 12) {
            playerLostBanner
            gimbalFailsafeBanner

            Picker("Mode", selection: Binding(
                get: { coordinator.mode },
                set: { coordinator.setMode($0) }
            )) {
                Text("Player Lock").tag(TrackingMode.playerLock)
                Text("Ball Track").tag(TrackingMode.ballTrack)
                Text("Smart Soccer").tag(TrackingMode.smartSoccer)
            }
            .pickerStyle(.segmented)

            HStack(spacing: 16) {
                Button(coordinator.isPaused ? "Resume Tracking" : "Pause Tracking") {
                    coordinator.isPaused ? coordinator.resumeTracking() : coordinator.pauseTracking()
                }
                Button("Center Gimbal") { coordinator.centerGimbal() }
                Button(isRecording ? "Stop Assessment" : "Start Assessment") {
                    let wasRecording = isRecording
                    isRecording.toggle()
                    if wasRecording {
                        onStopAssessment()
                        showPlayerTrackedConfirmation = true
                    } else {
                        onStartAssessment()
                    }
                }
                .bold()
                .foregroundStyle(isRecording ? .red : .green)
            }
            .buttonStyle(.bordered)

            Toggle("Field Test Mode", isOn: $isFieldTestMode)
                .font(.caption)
        }
        .padding()
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    // MARK: - Field Test Mode (spec section 32 — admin/debug only)

    private var fieldTestOverlay: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("TRACKING TEST MODE").bold()
            Text(String(format: "Inference FPS: %.1f", coordinator.inferenceFPS))
            Text("Dropped frames: \(coordinator.droppedFrameCount)")
            Text("Player state: \(coordinator.playerState.rawValue)")
            Text("Ball state: \(coordinator.ballState.rawValue)")
            Text("Gimbal: \(String(describing: dockKit.connectionState))")
            if let battery = dockKit.batteryLevel {
                Text(String(format: "Gimbal battery: %.0f%%", battery * 100))
            }
            Text("Thermal state: \(String(describing: coordinator.thermalManager.thermalState))")
            Text("Ball model: \(coordinator.modelVersionInfo.ballModelStatus)")
            if let timing = coordinator.lastStageTiming {
                Text(String(format: "Player: %.1fms  Ball: %.1fms  Pose: %.1fms  Total: %.1fms", timing.playerDetectionMs, timing.ballDetectionMs, timing.poseDetectionMs, timing.totalMs))
            }
        }
        .font(.system(.caption, design: .monospaced))
        .padding(8)
        .background(.black.opacity(0.6))
        .foregroundStyle(.green)
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
