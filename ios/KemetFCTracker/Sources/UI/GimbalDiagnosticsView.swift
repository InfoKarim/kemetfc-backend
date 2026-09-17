//
//  GimbalDiagnosticsView.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Read-only diagnostics for the physically connected DockKit accessory
//  (spec: "accessory detected/category/name/battery/firmware/tracking
//  capability/current state") — every field is read directly from
//  DockKitManager's own published state, never a separate guess, so
//  this screen can never show something inconsistent with what the
//  soccer-tracking screen is actually seeing.
//

import SwiftUI

public struct GimbalDiagnosticsView: View {
    @ObservedObject var dockKit: DockKitManager

    public init(dockKit: DockKitManager) {
        self.dockKit = dockKit
    }

    public var body: some View {
        List {
            Section("Accessory") {
                diagnosticRow("Detected", dockKit.isConnected ? "Yes" : "No")
                diagnosticRow("Name", dockKit.lastAccessoryName ?? "—")
                diagnosticRow("Category", dockKit.lastAccessoryCategory ?? "—")
                diagnosticRow("Firmware", dockKit.firmwareVersion ?? "—")
            }

            Section("Status") {
                diagnosticRow("Connection state", String(describing: dockKit.connectionState))
                diagnosticRow("Battery", dockKit.batteryLevel.map { String(format: "%.0f%%", $0 * 100) } ?? "—")
                diagnosticRow(
                    "Tracking button enabled",
                    dockKit.trackingButtonEnabled.map { $0 ? "Yes" : "No" } ?? "—"
                )
            }

            if let lastError = dockKit.lastError {
                Section("Last error") {
                    Text(lastError).foregroundStyle(.red)
                }
            }

            Section {
                Text("KEMET FC never enables Apple's built-in system tracking (DockAccessoryManager.isSystemTrackingEnabled stays false) — all gimbal motion is driven by this app's own player-lock logic, never by the accessory's own subject detection.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Gimbal Diagnostics")
    }

    private func diagnosticRow(_ label: String, _ value: String) -> some View {
        HStack {
            Text(label)
            Spacer()
            Text(value).foregroundStyle(.secondary)
        }
    }
}
