//
//  DockKitTestModeView.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  A DockKit sanity-check screen, DELIBERATELY SEPARATE from the soccer
//  Smart Tracking screen (spec: "a separate DockKit Test Mode screen ...
//  distinct from soccer tracking") — this lets a coach or engineer
//  verify the gimbal itself responds correctly (moves to a target,
//  centers, streams continuously) BEFORE trusting it during a real
//  assessment, without any player-detection/appearance-tracking logic
//  in the loop to confuse which layer a problem is in.
//
//  Uses its own GimbalController instance — not TrackingCoordinator's —
//  since the whole point is testing DockKit in isolation.
//

import AVFoundation
import SwiftUI

public struct DockKitTestModeView: View {
    @ObservedObject var dockKit: DockKitManager
    @ObservedObject var capture: CameraCaptureManager
    @StateObject private var gimbalController = GimbalController()

    @State private var isStreamingContinuous = false
    @State private var streamTask: Task<Void, Never>?
    @State private var lastErrorText: String?
    @State private var lastActionText: String?

    public init(dockKit: DockKitManager, capture: CameraCaptureManager) {
        self.dockKit = dockKit
        self.capture = capture
    }

    public var body: some View {
        List {
            Section("Connection") {
                HStack {
                    Text("Accessory")
                    Spacer()
                    Text(dockKit.isConnected ? "Connected" : "Not connected").foregroundStyle(.secondary)
                }
                Button("Attach Test Controller") {
                    guard let accessory = dockKit.connectedAccessory else {
                        lastErrorText = "No dock accessory connected — dock the Flow 2 Pro first."
                        return
                    }
                    gimbalController.attach(accessory: accessory)
                    lastActionText = "Attached test controller to \(accessory.identifier.name)"
                }
                .disabled(!dockKit.isConnected)
            }

            Section("Manual Moves") {
                Button("Center Gimbal") { gimbalController.centerGimbal() }
                Button("Move Target: Left") { sendTestTarget(x: 0.1, y: 0.4) }
                Button("Move Target: Center") { sendTestTarget(x: 0.4, y: 0.4) }
                Button("Move Target: Right") { sendTestTarget(x: 0.7, y: 0.4) }
                Button("Stop Motor Correction") { gimbalController.pauseMotorCorrection() }
            }

            Section("Continuous Observations") {
                Toggle("Stream synthetic sweep (15Hz)", isOn: Binding(
                    get: { isStreamingContinuous },
                    set: { newValue in newValue ? startContinuousStream() : stopContinuousStream() }
                ))
            }

            if let lastActionText {
                Section("Last action") {
                    Text(lastActionText)
                }
            }

            if let lastErrorText {
                Section("Last error") {
                    Text(lastErrorText).foregroundStyle(.red)
                }
            }
        }
        .navigationTitle("DockKit Test Mode")
        .onDisappear { stopContinuousStream() }
    }

    private func sendTestTarget(x: Double, y: Double) {
        guard let device = capture.device else {
            lastErrorText = "Camera not configured — open the assessment screen once first so a capture device is negotiated."
            return
        }
        Task {
            do {
                try await gimbalController.sendTestObservation(
                    rect: NormalizedRect(x: x, y: y, width: 0.2, height: 0.4),
                    captureDevice: device
                )
                lastActionText = "Sent static observation at (\(x), \(y))"
                lastErrorText = nil
            } catch {
                lastErrorText = "track(_:cameraInformation:) failed: \(error.localizedDescription)"
            }
        }
    }

    private func startContinuousStream() {
        guard let device = capture.device else {
            lastErrorText = "Camera not configured — open the assessment screen once first so a capture device is negotiated."
            return
        }
        isStreamingContinuous = true
        streamTask?.cancel()
        streamTask = Task {
            var t: Double = 0
            while !Task.isCancelled {
                // A slow left-right sweep — enough to visibly confirm
                // continuous tracking calls actually move the gimbal,
                // without the coach needing a real person/ball in frame.
                let x = 0.4 + 0.3 * sin(t)
                do {
                    try await gimbalController.sendTestObservation(
                        rect: NormalizedRect(x: x, y: 0.4, width: 0.2, height: 0.4),
                        captureDevice: device
                    )
                    await MainActor.run { lastErrorText = nil }
                } catch {
                    await MainActor.run { lastErrorText = "Continuous stream error: \(error.localizedDescription)" }
                }
                t += 0.3
                try? await Task.sleep(nanoseconds: UInt64(1.0 / 15.0 * 1_000_000_000))
            }
        }
    }

    private func stopContinuousStream() {
        isStreamingContinuous = false
        streamTask?.cancel()
        streamTask = nil
    }
}
