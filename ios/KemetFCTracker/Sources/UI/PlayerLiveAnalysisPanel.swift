//
//  PlayerLiveAnalysisPanel.swift
//  KemetFCTracker
//
//  Coach-facing replacement for FieldTestOverlay in the normal production
//  view (per PLAYER_ANALYSIS_CAPABILITY_AUDIT.md and its implementation
//  report). Reads ONLY `TrackingCoordinator.playerAnalysis`
//  (a `PlayerLiveAnalysisSnapshot` — see PlayerAnalysisEngine.swift) — this
//  view performs no calculation of its own, per "the UI must never calculate
//  CV/tactical metrics itself."
//
//  Never shows: backend URL, HTTP status, internal model names, thermal
//  state, or raw engineering diagnostics — those remain in FieldTestOverlay,
//  reachable only in DEBUG builds via Settings.
//
//  Never claims: tactical positioning, space awareness, decision making,
//  true scanning, possession, touches, passes, shots, or real-world
//  speed/distance. A value is shown only when the underlying snapshot says
//  it is known; otherwise "—" / "Unavailable" / "Collecting data".
//

import SwiftUI

struct PlayerLiveAnalysisPanel: View {
    let snapshot: PlayerLiveAnalysisSnapshot?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("PLAYER LIVE ANALYSIS")
                .font(.system(size: 11, weight: .bold))
                .tracking(0.5)
                .foregroundStyle(.white.opacity(0.85))

            row(label: "Tracking", value: trackingText, dotColor: trackingDotColor)
            row(label: "Movement", value: movementText)
            row(label: "Ball", value: ballVisibleText)
            row(label: "Ball proximity", value: ballProximityText)
        }
        .padding(12)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 12))
    }

    private func row(label: String, value: String, dotColor: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(.system(size: 9, weight: .medium))
                .foregroundStyle(.white.opacity(0.55))
            HStack(spacing: 6) {
                if let dotColor {
                    Circle().fill(dotColor).frame(width: 7, height: 7)
                }
                Text(value)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
    }

    // MARK: - Text derivation (labels only — no calculation)

    private var trackingText: String {
        guard let snapshot else { return "Collecting data" }
        switch snapshot.trackingState {
        case .locked: return "Locked"
        case .temporarilyLost: return "Temporarily lost"
        case .lost: return "Lost"
        case .unavailable: return "Unavailable"
        }
    }

    private var trackingDotColor: Color? {
        guard let snapshot else { return nil }
        switch snapshot.trackingState {
        case .locked: return .green
        case .temporarilyLost: return .orange
        case .lost: return .red
        case .unavailable: return nil
        }
    }

    private var movementText: String {
        guard let snapshot else { return "Collecting data" }
        switch snapshot.movementState {
        case .moving: return "Moving"
        case .stationary: return "Stationary"
        case .uncertain: return "—"
        }
    }

    private var ballVisibleText: String {
        guard let snapshot else { return "Collecting data" }
        return snapshot.ballVisible ? "Visible" : "Not visible"
    }

    private var ballProximityText: String {
        guard let snapshot else { return "Collecting data" }
        switch snapshot.ballProximityState {
        case .near: return "Near"
        case .away: return "Away"
        case .unavailable: return "—"
        }
    }
}
