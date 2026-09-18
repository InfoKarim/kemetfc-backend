//
//  PlayerTrackingOverlay.swift
//  KemetFCTracker
//
//  Draws every currently-detected person as a thin neutral outline, and
//  the LOCKED athlete with premium corner brackets + a real (never
//  fabricated) confidence readout — reading directly from
//  TrackingCoordinator's own published state, the same values already
//  driving telemetry (spec section 14: "Use actual application state").
//

import SwiftUI

struct PlayerTrackingOverlay: View {
    let detections: [PersonDetection]
    let lockedBox: NormalizedRect?
    let playerState: PlayerTrackState
    let confidence: Double?
    let playerFirstName: String?

    /// Smoothed on-screen box — inference runs at ~15fps while the
    /// display refreshes at 60fps; without this the bracket would jump
    /// between discrete detector updates (spec: "Do not make the
    /// interface visually jump ... Interpolate UI position for smooth
    /// movement"). SwiftUI's own implicit animation on this @State value
    /// does the actual interpolation — never a fabricated position.
    @State private var displayedLockedRect: CGRect?

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            ZStack {
                ForEach(Array(unlockedDetections.enumerated()), id: \.offset) { _, detection in
                    UnselectedDetectionOutline()
                        .frame(
                            width: detection.boundingBox.width * size.width,
                            height: detection.boundingBox.height * size.height
                        )
                        .position(
                            x: (detection.boundingBox.x + detection.boundingBox.width / 2) * size.width,
                            y: (detection.boundingBox.y + detection.boundingBox.height / 2) * size.height
                        )
                }

                if playerState != .lost, let rect = displayedLockedRect {
                    VStack(spacing: 6) {
                        lockedLabel
                        TrackingCornerBrackets()
                            .frame(width: rect.width, height: rect.height)
                    }
                    .position(x: rect.midX, y: rect.midY - 18)
                    .animation(.easeInOut(duration: 0.12), value: rect)
                }
            }
            .onChange(of: lockedBox) { _, newValue in
                guard let newValue else {
                    displayedLockedRect = nil
                    return
                }
                withAnimation(.easeInOut(duration: 0.12)) {
                    displayedLockedRect = CGRect(
                        x: newValue.x * size.width,
                        y: newValue.y * size.height,
                        width: newValue.width * size.width,
                        height: newValue.height * size.height
                    )
                }
            }
        }
        .allowsHitTesting(false)
    }

    /// Detections that aren't the currently-locked player — a rough IoU
    /// check against the last known locked box, since raw detections
    /// don't carry the tracker's own track ID.
    private var unlockedDetections: [PersonDetection] {
        guard playerState != .lost, let lockedBox else { return detections }
        return detections.filter { $0.boundingBox.iou(with: lockedBox) < 0.5 }
    }

    private var lockedLabel: some View {
        VStack(spacing: 1) {
            Text(playerState == .locked ? "PLAYER LOCKED" : "REACQUIRING")
                .font(.system(size: 11, weight: .bold))
                .tracking(0.5)
            if let confidence {
                Text("\(Int(confidence * 100))%")
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
            }
            if let playerFirstName {
                Text(playerFirstName)
                    .font(.system(size: 10, weight: .medium))
                    .opacity(0.85)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(.black.opacity(0.45), in: Capsule())
        .overlay(Capsule().stroke(Color.kemetGold.opacity(0.6), lineWidth: 1))
    }
}
