//
//  RecordingControl.swift
//  KemetFCTracker
//
//  The dominant record/stop control (spec section 5: "RECORD should be
//  the dominant control", section 6: recording state must be
//  immediately understandable, reachable one-handed).
//

import SwiftUI

struct RecordingControl: View {
    let isRecording: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .stroke(.white, lineWidth: 4)
                    .frame(width: 68, height: 68)
                if isRecording {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(.red)
                        .frame(width: 26, height: 26)
                } else {
                    Circle()
                        .fill(.red)
                        .frame(width: 56, height: 56)
                }
            }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(isRecording ? "Stop Recording" : "Start Recording")
    }
}
