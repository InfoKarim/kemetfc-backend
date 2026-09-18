//
//  FieldTestOverlay.swift
//  KemetFCTracker
//
//  Technical diagnostics (spec section 10) — moved out of the always-on
//  production HUD entirely. Only ever shown when Field Test Mode is
//  explicitly enabled in Settings (spec section 15: production and
//  debug views must never mix). Every value here reads a real published
//  property already used elsewhere (telemetry, the old inline debug
//  overlay) — nothing here is fabricated for display purposes.
//

import SwiftUI

struct FieldTestOverlay: View {
    let captureFPS: Double
    let inferenceFPS: Double
    let playerConfidence: Double?
    let trackId: Int?
    let droppedFrameCount: Int
    let gimbalState: GimbalConnectionState
    let gimbalBatteryLevel: Double?
    let ballConfidence: Double?
    let ballModelStatus: String
    let latencyMs: Double?
    let thermalState: ProcessInfo.ThermalState
    let backendDiagnostics: BackendDiagnostics
    let trackingAvailable: Bool
    let currentUploadState: PendingAssessmentRecord.UploadState?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text("FIELD TEST MODE").bold()
            row("BACKEND", backendDiagnostics.isReachable == true ? "CONNECTED" : (backendDiagnostics.isReachable == false ? "OFFLINE" : "UNKNOWN"))
            row("BASE URL", backendDiagnostics.baseURL.absoluteString)
            row("TRACKING", trackingAvailable ? "AVAILABLE" : "UNAVAILABLE")
            if let currentUploadState {
                row("UPLOAD", uploadStatusText(currentUploadState))
            }
            if let lastRequest = backendDiagnostics.lastRequestDescription {
                row("LAST REQUEST", backendDiagnostics.lastErrorDescription.map { "\(lastRequest) — \($0)" } ?? "\(lastRequest) OK")
            }
            if let backendLatency = backendDiagnostics.lastLatencyMs {
                row("LATENCY", String(format: "%.0fms", backendLatency))
            }
            row("FPS", String(format: "%.1f", captureFPS))
            row("Inference FPS", String(format: "%.1f", inferenceFPS))
            row("Player confidence", playerConfidence.map { String(format: "%.0f%%", $0 * 100) } ?? "—")
            row("Track ID", trackId.map(String.init) ?? "—")
            row("Lost frames", "\(droppedFrameCount)")
            row("Gimbal", gimbalStatusText)
            if let gimbalBatteryLevel {
                row("Gimbal battery", String(format: "%.0f%%", gimbalBatteryLevel * 100))
            }
            row("Ball confidence", ballConfidence.map { String(format: "%.0f%%", $0 * 100) } ?? "—")
            row("Ball model", ballModelStatus)
            if let latencyMs {
                row("Latency", String(format: "%.1fms", latencyMs))
            }
            row("Thermal state", thermalStateText)
        }
        .font(.system(.caption, design: .monospaced))
        .padding(8)
        .background(.black.opacity(0.6))
        .foregroundStyle(.green)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack {
            Text("\(label):")
            Spacer(minLength: 8)
            Text(value)
        }
    }

    private var gimbalStatusText: String {
        switch gimbalState {
        case .connected: return "connected"
        case .trackingActive: return "tracking_active"
        case .trackingLost: return "tracking_lost"
        case .reconnecting: return "reconnecting"
        case .disconnected: return "disconnected"
        }
    }

    private func uploadStatusText(_ state: PendingAssessmentRecord.UploadState) -> String {
        switch state {
        case .recording, .localSaved: return "PENDING"
        case .pendingUpload, .uploading, .failedRetryable: return "RETRYING"
        case .uploaded: return "SYNCED"
        case .failedPermanent: return "FAILED"
        }
    }

    private var thermalStateText: String {
        switch thermalState {
        case .nominal: return "nominal"
        case .fair: return "fair"
        case .serious: return "serious"
        case .critical: return "critical"
        @unknown default: return "unknown"
        }
    }
}
