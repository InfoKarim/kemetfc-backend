//
//  ConnectionStatusView.swift
//  KemetFCTracker
//
//  Replaces the old large "GIMBAL NOT FOUND" badge (spec section 8) with
//  a small tappable indicator — tracking stays fully usable with the
//  gimbal offline, so this must never dominate the interface.
//

import SwiftUI

struct ConnectionStatusView: View {
    let state: GimbalConnectionState
    let batteryLevel: Double?
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 5) {
                Circle().fill(dotColor).frame(width: 7, height: 7)
                Text("GIMBAL")
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(0.5)
            }
            .foregroundStyle(.white.opacity(0.85))
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial, in: Capsule())
        }
        .buttonStyle(.plain)
    }

    private var dotColor: Color {
        switch state {
        case .connected, .trackingActive: return .green
        case .reconnecting: return .yellow
        case .disconnected, .trackingLost: return .white.opacity(0.35)
        }
    }
}

struct GimbalDetailSheet: View {
    let state: GimbalConnectionState
    let batteryLevel: Double?
    let lastAccessoryName: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Gimbal").font(.headline)
            LabeledContent("Status", value: statusText)
            if let lastAccessoryName {
                LabeledContent("Accessory", value: lastAccessoryName)
            }
            if let batteryLevel {
                LabeledContent("Battery", value: "\(Int(batteryLevel * 100))%")
            }
            Text("Tracking and recording continue normally even while the gimbal is offline.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding()
        .presentationDetents([.fraction(0.3)])
    }

    private var statusText: String {
        switch state {
        case .connected: return "Connected"
        case .trackingActive: return "Tracking Active"
        case .trackingLost: return "Tracking Lost"
        case .reconnecting: return "Reconnecting"
        case .disconnected: return "Not Connected"
        }
    }
}
