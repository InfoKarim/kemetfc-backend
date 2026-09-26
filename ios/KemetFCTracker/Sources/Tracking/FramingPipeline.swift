//
//  FramingPipeline.swift
//  KemetFCTracker
//
//  The framing target that TrackingCoordinator actually sends to the gimbal.
//  Pure and Foundation-only so the production path is unit-testable.
//
//  DECISION on FramingCalculator.dampedCorrection — USE IT. It was tested but
//  never called. DockKit accepts a target rectangle every ~0.1 s and moves the
//  gimbal toward it, so raw per-frame detection jitter would become motor
//  chatter. FramingStabilizer therefore treats the last rectangle it SENT as
//  the "current" framing and applies the calculator's dead zone (ignore small
//  changes) and damping (move only part of the way) before anything is sent.
//  (Both rules also cap how far a single update can move the target.)
//
//  Safety rules, all covered by FramingPipelineTests:
//    * The selected player is the anchor. When the player was NOT observed this
//      frame the pipeline HOLDS the last framing — a detected ball never pulls
//      the camera away from a player the tracker has lost.
//    * Ball loss never drops the player: the ball is simply left out.
//    * A ball influences framing only when its detection is confident enough and
//      near the player; a far-away bright object (a cone, a false positive) cannot
//      yank the camera across the pitch.
//    * The target never moves more than `maxCenterStep` in one update.
//    * Nothing is invented: with no target ever established, the result is nil.
//

import Foundation

public struct FramingStabilizer: Sendable {
    /// Largest movement of the target center per update (normalized units).
    public var maxCenterStep = 0.12
    /// Damping applied to the target's size (its center uses dampedCorrection).
    public var sizeDamping = 0.35
    public private(set) var lastSent: NormalizedRect?

    public init() {}

    public mutating func reset() { lastSent = nil }

    /// The last framing, unchanged — used while the player is not observed.
    public func hold() -> NormalizedRect? { lastSent }

    public mutating func stabilize(_ raw: NormalizedRect) -> NormalizedRect {
        guard let previous = lastSent else {
            lastSent = raw
            return raw
        }

        // FramingCalculator.dampedCorrection: nil inside the dead zone (keep the
        // current center), otherwise a partial step toward the target.
        var center = previous.center
        if let corrected = FramingCalculator.dampedCorrection(currentGimbalCenter: previous.center, targetCenter: raw.center) {
            let step = previous.center.distance(to: corrected)
            if step > maxCenterStep, step > 0 {
                let scale = maxCenterStep / step
                center = NormalizedPoint(
                    x: previous.center.x + (corrected.x - previous.center.x) * scale,
                    y: previous.center.y + (corrected.y - previous.center.y) * scale
                )
            } else {
                center = corrected
            }
        }

        let width = previous.width + (raw.width - previous.width) * sizeDamping
        let height = previous.height + (raw.height - previous.height) * sizeDamping
        let stabilized = NormalizedRect(
            x: min(max(center.x - width / 2, 0), 1 - width),
            y: min(max(center.y - height / 2, 0), 1 - height),
            width: width, height: height
        )
        lastSent = stabilized
        return stabilized
    }
}

public struct FramingPipeline: Sendable {
    public var padding = 0.08
    public var leadRoomMaxOffsetFraction = 0.15
    /// A ball this unsure never influences framing (the classical detector tops out at 0.40).
    public var minimumBallConfidence = 0.25
    /// A ball farther than this from the player (center distance) is ignored for framing.
    public var maxBallDistanceFromPlayer = 0.45
    public private(set) var stabilizer = FramingStabilizer()

    public init() {}

    public mutating func reset() { stabilizer.reset() }

    /// - Returns: the rectangle to send to the gimbal, or nil when no framing has
    ///   ever been established.
    public mutating func nextTarget(
        mode: TrackingMode,
        playerBox: NormalizedRect?,
        playerObserved: Bool,
        ballBox: NormalizedRect?,
        ballConfidence: Double,
        playerVelocity: NormalizedPoint
    ) -> NormalizedRect? {
        let usableBall: NormalizedRect? = {
            guard let ballBox, ballConfidence >= minimumBallConfidence else { return nil }
            if let playerBox, playerBox.center.distance(to: ballBox.center) > maxBallDistanceFromPlayer { return nil }
            return ballBox
        }()

        let raw: NormalizedRect?
        switch mode {
        case .playerLock:
            raw = playerObserved ? playerBox : nil
        case .ballTrack:
            raw = usableBall
        case .smartSoccer:
            // The player anchors the frame; the ball may only extend it.
            raw = playerObserved
                ? FramingCalculator.combinedFramingTarget(player: playerBox, ball: usableBall, padding: padding)
                : nil
        }

        guard let raw else { return stabilizer.hold() }   // not observed: hold, never chase

        let withLeadRoom = FramingCalculator.applyLeadRoom(
            to: raw, playerVelocity: playerVelocity, maxOffsetFraction: leadRoomMaxOffsetFraction
        )
        return stabilizer.stabilize(FramingCalculator.enforceMinimumFrameSize(withLeadRoom))
    }
}
