//
//  AssessmentControlDock.swift
//  KemetFCTracker
//
//  The compact floating glass dock replacing the old large gray control
//  panel (spec section 5) — primary controls only: Tracking Mode,
//  RECORD (dominant), Target, plus a small settings affordance for
//  everything else (pause/resume tracking, center gimbal, Field Test
//  Mode). While recording, the secondary controls minimize so nothing
//  competes with the record/stop button or obstructs the athlete (spec
//  section 6).
//

import SwiftUI

struct AssessmentControlDock: View {
    let mode: TrackingMode
    let ballModelAvailable: Bool
    let isRecording: Bool
    let onSelectMode: (TrackingMode) -> Void
    let onUnavailableModeSelected: () -> Void
    let onRecordToggle: () -> Void
    let onSelectPlayer: () -> Void
    let onReselectPlayer: () -> Void
    let onOpenSettings: () -> Void

    var body: some View {
        HStack(spacing: 0) {
            if !isRecording {
                TrackingModeSelector(
                    mode: mode,
                    ballModelAvailable: ballModelAvailable,
                    onSelect: onSelectMode,
                    onUnavailableModeSelected: onUnavailableModeSelected
                )
                Spacer()
            } else {
                Spacer()
            }

            RecordingControl(isRecording: isRecording, action: onRecordToggle)

            Spacer()

            if !isRecording {
                targetMenu
            } else {
                Color.clear.frame(width: 56, height: 44)
            }

            Button(action: onOpenSettings) {
                Image(systemName: "ellipsis")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white.opacity(isRecording ? 0.4 : 0.85))
                    .frame(width: 36, height: 44)
            }
            .disabled(isRecording)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 26))
        .animation(.easeInOut(duration: 0.2), value: isRecording)
    }

    private var targetMenu: some View {
        Menu {
            Button("Select Player", action: onSelectPlayer)
            Button("Reselect Player", action: onReselectPlayer)
        } label: {
            VStack(spacing: 2) {
                Image(systemName: "viewfinder")
                    .font(.system(size: 17, weight: .medium))
                Text("TARGET")
                    .font(.system(size: 9, weight: .medium))
            }
            .foregroundStyle(.white)
            .frame(width: 56, height: 44)
        }
    }
}
