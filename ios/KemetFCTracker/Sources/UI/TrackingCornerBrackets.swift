//
//  TrackingCornerBrackets.swift
//  KemetFCTracker
//
//  The premium tracking frame drawn around the LOCKED athlete (spec:
//  "four corner brackets around the athlete with subtle animation" —
//  never a thick full rectangle, which reads as a developer debug
//  overlay rather than a professional sports-camera product).
//

import SwiftUI

struct TrackingCornerBrackets: View {
    var color: Color = .kemetGold
    var cornerLength: CGFloat = 22
    var lineWidth: CGFloat = 3

    @State private var pulse = false

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            ZStack {
                bracket(at: .topLeading, in: size)
                bracket(at: .topTrailing, in: size)
                bracket(at: .bottomLeading, in: size)
                bracket(at: .bottomTrailing, in: size)
            }
        }
        .opacity(pulse ? 1.0 : 0.85)
        .onAppear {
            withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) {
                pulse = true
            }
        }
    }

    private func bracket(at corner: UnitPoint, in size: CGSize) -> some View {
        Path { path in
            switch corner {
            case .topLeading:
                path.move(to: CGPoint(x: 0, y: cornerLength))
                path.addLine(to: .zero)
                path.addLine(to: CGPoint(x: cornerLength, y: 0))
            case .topTrailing:
                path.move(to: CGPoint(x: size.width - cornerLength, y: 0))
                path.addLine(to: CGPoint(x: size.width, y: 0))
                path.addLine(to: CGPoint(x: size.width, y: cornerLength))
            case .bottomLeading:
                path.move(to: CGPoint(x: 0, y: size.height - cornerLength))
                path.addLine(to: CGPoint(x: 0, y: size.height))
                path.addLine(to: CGPoint(x: cornerLength, y: size.height))
            default: // .bottomTrailing
                path.move(to: CGPoint(x: size.width - cornerLength, y: size.height))
                path.addLine(to: CGPoint(x: size.width, y: size.height))
                path.addLine(to: CGPoint(x: size.width, y: size.height - cornerLength))
            }
        }
        .stroke(color, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
        .shadow(color: color.opacity(0.6), radius: 4)
    }
}

/// A thin, neutral outline for a detected-but-not-selected person — never
/// as prominent as the locked athlete's brackets (spec: "Unselected
/// detected person: thin neutral outline").
struct UnselectedDetectionOutline: View {
    var body: some View {
        RoundedRectangle(cornerRadius: 6)
            .stroke(Color.white.opacity(0.35), lineWidth: 1.5)
    }
}
