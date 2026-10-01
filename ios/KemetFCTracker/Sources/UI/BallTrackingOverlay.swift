//
//  BallTrackingOverlay.swift
//  KemetFCTracker
//
//  Real production ball-position marker (2026-09-29 production incident
//  fix — BallTracker already detects and tracks the ball every frame,
//  confirmed by real telemetry from a real 15-minute match, but NO
//  overlay has ever shown this on screen; only a numeric confidence
//  readout existed, and only inside the DEBUG-only Field Test overlay).
//  This view performs NO detection of its own — it only reads
//  TrackingCoordinator.ballBoundingBox/ballConfidence, the SAME
//  BallTracker.currentBoundingBox/currentConfidence values already
//  driving telemetry and framing elsewhere (same principle as
//  PlayerTrackingOverlay's own header comment: "Use actual application
//  state").
//
//  Coordinate transform mirrors PlayerTrackingOverlay exactly: a
//  normalized (0...1, top-left origin) NormalizedRect scaled by this
//  view's own GeometryReader size — the SAME space the camera preview
//  fills (aspect-fill), so no separate crop/orientation math is needed
//  beyond what PlayerTrackingOverlay already relies on.
//

import SwiftUI

/// Pure geometry/opacity helpers, extracted so they're directly testable
/// without instantiating SwiftUI view state (same pattern as
/// FramingMathTests' own pure-math-extraction convention).
enum BallOverlayGeometry {
    /// Normalized (0...1, top-left origin) → preview-space conversion —
    /// identical math to PlayerTrackingOverlay's own inline version.
    /// `size` is the SAME GeometryReader size the camera preview fills
    /// (aspect-fill), so no separate crop step exists for either overlay
    /// — confirmed by auditing PlayerTrackingOverlay, which does the
    /// same direct scale with no additional crop/orientation math.
    static func screenRect(for box: NormalizedRect, in size: CGSize) -> CGRect {
        CGRect(
            x: box.x * size.width,
            y: box.y * size.height,
            width: box.width * size.width,
            height: box.height * size.height
        )
    }

    /// Scaled against the ball detector's documented ~0.40 confidence
    /// ceiling (classical heuristic, not a calibrated probability) rather
    /// than against 1.0, so a real, typical detection doesn't render
    /// artificially faint — never reaches full opacity for a nil/zero
    /// confidence, and never implies more certainty than the detector
    /// itself claims.
    static func markerOpacity(confidence: Double?) -> Double {
        let value = confidence ?? 0
        return 0.35 + 0.65 * min(1, max(0, value / 0.4))
    }
}

struct BallTrackingOverlay: View {
    /// nil means "not currently tracked" (BallTracker's own documented
    /// semantics — DETECTED vs NOT DETECTED are distinct) — the marker
    /// must disappear the SAME frame this goes nil, never linger.
    let ballBox: NormalizedRect?
    let confidence: Double?

    /// Smoothed on-screen position, same reasoning as
    /// PlayerTrackingOverlay.displayedLockedRect — inference runs slower
    /// than display refresh, so this interpolates between discrete
    /// detector updates. Sets to nil (removing the marker immediately,
    /// no fade-out lingering) the instant `ballBox` goes nil.
    @State private var displayedRect: CGRect?

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            ZStack {
                if let rect = displayedRect {
                    BallReticle()
                        .frame(width: rect.width, height: rect.height)
                        .position(x: rect.midX, y: rect.midY)
                        // Never implies false certainty: a low-confidence
                        // detection is drawn fainter, never relabeled as
                        // "certain" or given a numeric readout here.
                        .opacity(markerOpacity)
                        .animation(.easeInOut(duration: 0.12), value: rect)
                }
            }
            .onChange(of: ballBox) { _, newValue in
                guard let newValue else {
                    // Immediate removal — no stale marker after track loss.
                    displayedRect = nil
                    return
                }
                withAnimation(.easeInOut(duration: 0.12)) {
                    displayedRect = BallOverlayGeometry.screenRect(for: newValue, in: size)
                }
            }
        }
        .allowsHitTesting(false)
    }

    private var markerOpacity: Double {
        BallOverlayGeometry.markerOpacity(confidence: confidence)
    }
}

/// Lightweight, deliberately distinct from TrackingCornerBrackets (the
/// locked-PLAYER marker) — a small circular reticle, never a confident
/// rectangle with a percentage label, matching "keep visual treatment
/// lightweight" and "do not label low-confidence detections as certain."
private struct BallReticle: View {
    var body: some View {
        Circle()
            .strokeBorder(Color.kemetGold, lineWidth: 2)
            .background(Circle().fill(Color.kemetGold.opacity(0.15)))
    }
}
