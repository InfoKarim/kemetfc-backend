//
//  AssessmentHUD.swift
//  KemetFCTracker
//
//  The compact floating glass HUD (spec section 2) — brand mark, the
//  ACTUAL confirmed player's name/age-group/team (never a placeholder),
//  and a REC + elapsed-timer readout that only appears while genuinely
//  recording. Tiny status dots below summarize tracking/gimbal/AI state
//  without any long technical message ever appearing here — that detail
//  lives only in Field Test Mode (FieldTestOverlay.swift).
//

import SwiftUI

struct AssessmentHUD: View {
    let player: KemetPlayerSummary
    let isRecording: Bool
    let elapsedSeconds: Int
    let playerState: PlayerTrackState
    let gimbalState: GimbalConnectionState
    let ballModelAvailable: Bool

    var body: some View {
        VStack(spacing: 6) {
            HStack(alignment: .top) {
                brandMark
                Spacer()
                playerIdentity
                Spacer()
                recIndicator
            }
            HStack(spacing: 14) {
                statusDot(label: "Tracking", isActive: playerState != .lost)
                statusDot(label: "Gimbal", isActive: gimbalState == .connected || gimbalState == .trackingActive)
                statusDot(label: "AI", isActive: ballModelAvailable)
                Spacer()
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
        .environment(\.colorScheme, .dark)
    }

    private var brandMark: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("KEMET FC").font(.system(size: 13, weight: .bold)).tracking(0.5)
            Text("ASSESSMENT").font(.system(size: 9, weight: .medium)).tracking(1).opacity(0.6)
        }
        .foregroundStyle(.white)
    }

    private var playerIdentity: some View {
        VStack(spacing: 0) {
            Text(player.fullName.uppercased())
                .font(.system(size: 13, weight: .semibold))
                .lineLimit(1)
            let secondary = [player.ageGroup, player.teamName].compactMap { $0 }.joined(separator: " · ")
            if !secondary.isEmpty {
                Text(secondary)
                    .font(.system(size: 10, weight: .regular))
                    .opacity(0.65)
            }
        }
        .foregroundStyle(.white)
    }

    @ViewBuilder
    private var recIndicator: some View {
        if isRecording {
            HStack(spacing: 5) {
                Circle().fill(.red).frame(width: 8, height: 8)
                Text(elapsedTimeString)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .monospacedDigit()
            }
            .foregroundStyle(.white)
        } else {
            Color.clear.frame(width: 1, height: 1)
        }
    }

    private var elapsedTimeString: String {
        let minutes = elapsedSeconds / 60
        let seconds = elapsedSeconds % 60
        return String(format: "%02d:%02d", minutes, seconds)
    }

    private func statusDot(label: String, isActive: Bool) -> some View {
        HStack(spacing: 4) {
            Circle()
                .fill(isActive ? Color.kemetGold : Color.white.opacity(0.3))
                .frame(width: 6, height: 6)
            Text(label)
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(.white.opacity(0.6))
        }
    }
}
