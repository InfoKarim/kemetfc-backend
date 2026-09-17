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

    let onTapToLock: (NormalizedPoint) -> Void
    let onStartAssessment: () -> Void
    let onStopAssessment: () -> Void

    public init(
        capture: CameraCaptureManager,
        dockKit: DockKitManager,
        coordinator: TrackingCoordinator,
        onTapToLock: @escaping (NormalizedPoint) -> Void,
        onStartAssessment: @escaping () -> Void,
        onStopAssessment: @escaping () -> Void
    ) {
        self.capture = capture
        self.dockKit = dockKit
        self.coordinator = coordinator
        self.onTapToLock = onTapToLock
        self.onStartAssessment = onStartAssessment
        self.onStopAssessment = onStopAssessment
    }

    public var body: some View {
        ZStack {
            CameraPreviewRepresentable(session: capture.session)
                .ignoresSafeArea()
                .overlay(trackingOverlay)
                .onTapGesture { location in
                    // Converting a raw view-space tap into a normalized
                    // (0...1) image-space point is display-layer glue
                    // (needs the actual preview layer's videoRect) —
                    // shown as a placeholder call here; wire the real
                    // AVCaptureVideoPreviewLayer.captureDevicePointConverted
                    // conversion in CameraPreviewRepresentable before
                    // shipping.
                    onTapToLock(NormalizedPoint(x: 0.5, y: 0.5))
                }

            VStack {
                topStatusBar
                Spacer()
                if isFieldTestMode { fieldTestOverlay }
                bottomControls
            }
            .padding()
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
            Spacer()
            if isRecording {
                Label("REC", systemImage: "circle.fill").foregroundStyle(.red).bold()
            }
        }
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

    // MARK: - Bottom controls

    private var bottomControls: some View {
        VStack(spacing: 12) {
            playerLostBanner

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
                    isRecording.toggle()
                    isRecording ? onStartAssessment() : onStopAssessment()
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
