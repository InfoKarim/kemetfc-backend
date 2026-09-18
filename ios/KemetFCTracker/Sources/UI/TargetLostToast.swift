//
//  TargetLostToast.swift
//  KemetFCTracker
//
//  Replaces the old full-width red "PLAYER LOST — TAP TO RESELECT" panel
//  (spec section 7) with a small, restrained floating notification that
//  never blocks the athlete/camera.
//

import SwiftUI

enum TargetToastKind {
    case lost
    case reacquired
}

struct TargetLostToast: View {
    let kind: TargetToastKind

    var body: some View {
        VStack(spacing: 2) {
            Text(kind == .lost ? "TARGET LOST" : "TARGET REACQUIRED")
                .font(.system(size: 12, weight: .bold))
                .tracking(0.5)
            if kind == .lost {
                Text("Tap athlete to reacquire")
                    .font(.system(size: 11, weight: .regular))
                    .opacity(0.8)
            }
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(
            (kind == .lost ? Color.orange : Color.kemetGold).opacity(0.85),
            in: RoundedRectangle(cornerRadius: 14)
        )
        .shadow(color: .black.opacity(0.25), radius: 8, y: 2)
    }
}
