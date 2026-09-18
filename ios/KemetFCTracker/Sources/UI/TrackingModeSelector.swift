//
//  TrackingModeSelector.swift
//  KemetFCTracker
//
//  Compact control (spec section 5) that opens a menu of the three real
//  tracking modes rather than permanently showing all three as a
//  segmented control. BALL/SMART SOCCER are disabled (not hidden) when
//  no ball model is installed at all — tapping a disabled option
//  explains why, matching spec section 9 ("explain why only when the
//  user attempts to select them"); a genuinely available but
//  lower-accuracy fallback model is never disabled or specially flagged
//  here — that nuance is Field Test Mode's job, not production UI's.
//

import SwiftUI

struct TrackingModeSelector: View {
    let mode: TrackingMode
    let ballModelAvailable: Bool
    let onSelect: (TrackingMode) -> Void
    let onUnavailableModeSelected: () -> Void

    var body: some View {
        Menu {
            Button {
                onSelect(.playerLock)
            } label: {
                Label("Player", systemImage: mode == .playerLock ? "checkmark" : "person.fill")
            }
            modeButton(.ballTrack, title: "Ball", systemImage: "soccerball")
            modeButton(.smartSoccer, title: "Smart Soccer", systemImage: "sparkles")
        } label: {
            VStack(spacing: 2) {
                Image(systemName: modeIcon)
                    .font(.system(size: 17, weight: .medium))
                Text(modeLabel)
                    .font(.system(size: 9, weight: .medium))
            }
            .foregroundStyle(.white)
            .frame(width: 56, height: 44)
        }
    }

    @ViewBuilder
    private func modeButton(_ target: TrackingMode, title: String, systemImage: String) -> some View {
        if ballModelAvailable {
            Button {
                onSelect(target)
            } label: {
                Label(title, systemImage: mode == target ? "checkmark" : systemImage)
            }
        } else {
            Button(role: .none) {
                onUnavailableModeSelected()
            } label: {
                Label("\(title) (Unavailable)", systemImage: "exclamationmark.circle")
            }
        }
    }

    private var modeIcon: String {
        switch mode {
        case .playerLock: return "person.fill"
        case .ballTrack: return "soccerball"
        case .smartSoccer: return "sparkles"
        }
    }

    private var modeLabel: String {
        switch mode {
        case .playerLock: return "PLAYER"
        case .ballTrack: return "BALL"
        case .smartSoccer: return "SMART"
        }
    }
}
