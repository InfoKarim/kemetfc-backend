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
    // Kept unconditionally so the call site's shape doesn't need to change per
    // build config — the Toggle that actually surfaces this (below) is itself
    // DEBUG-gated, so this can never become true, and FieldTestOverlay can
    // never be shown, in a Release build.
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
                #if DEBUG
                Section {
                    Toggle("Field Test Mode", isOn: $isFieldTestMode)
                } footer: {
                    Text("DEBUG ONLY — raw engineering diagnostics (backend URL, HTTP status, FPS, latency, model names). Never shown in a production build; coaches see Player Live Analysis instead.")
                }
                #endif
            }
            .navigationTitle("Assessment Settings")
            .navigationBarTitleDisplayMode(.inline)
        }
        .presentationDetents([.fraction(0.4)])
    }
}
