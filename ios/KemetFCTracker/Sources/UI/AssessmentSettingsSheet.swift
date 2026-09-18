//
//  AssessmentSettingsSheet.swift
//  KemetFCTracker
//
//  Advanced controls that don't belong in the always-visible production
//  dock (spec section 5: "small more/settings icon for advanced
//  controls"; section 10: Field Test Mode moved here rather than being
//  a permanent toggle in the main view).
//

import SwiftUI

struct AssessmentSettingsSheet: View {
    @Binding var isFieldTestMode: Bool
    let isPaused: Bool
    let onPauseToggle: () -> Void
    let onCenterGimbal: () -> Void

    var body: some View {
        NavigationStack {
            List {
                Section("Tracking") {
                    Button(isPaused ? "Resume Tracking" : "Pause Tracking", action: onPauseToggle)
                    Button("Center Gimbal", action: onCenterGimbal)
                }
                Section {
                    Toggle("Field Test Mode", isOn: $isFieldTestMode)
                } footer: {
                    Text("Shows real-time technical diagnostics (FPS, confidence, latency) over the camera — for coaches diagnosing tracking issues, not everyday use.")
                }
            }
            .navigationTitle("Assessment Settings")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.fraction(0.4)])
    }
}
