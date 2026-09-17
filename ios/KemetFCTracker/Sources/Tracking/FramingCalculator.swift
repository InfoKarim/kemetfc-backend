//
//  FramingCalculator.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift
//  header for the module-wide caveat. UNLIKE most of this module, the
//  pure functions in this file take no Vision/DockKit/AVFoundation
//  types at all and are exercised by
//  Tests/FramingMathTests.swift — this file's *logic* has been checked
//  by running those tests with `swift test` in a scratch SwiftPM
//  package (not this app target, which needs Xcode/iOS SDK); see
//  ios/KemetFCTracker/README.md for exactly what that proves and
//  doesn't prove.
//
//  Pure math for: combining player+ball bounding boxes into one framing
//  target (spec section 9), lead-room offset in the player's direction
//  of motion (spec section 15), and dead-zone/damping for smooth gimbal
//  correction (spec section 11).
//

import Foundation

public enum FramingCalculator {
    /// The combined player+ball region with configurable padding — the
    /// SMART SOCCER mode target (spec section 9). Returns nil only when
    /// neither box is available (nothing to frame).
    public static func combinedFramingTarget(
        player: NormalizedRect?,
        ball: NormalizedRect?,
        padding: Double = 0.08
    ) -> NormalizedRect? {
        let boxes = [player, ball].compactMap { $0 }
        guard !boxes.isEmpty else { return nil }

        let minX = boxes.map(\.x).min()!
        let minY = boxes.map(\.y).min()!
        let maxX = boxes.map { $0.x + $0.width }.max()!
        let maxY = boxes.map { $0.y + $0.height }.max()!

        let paddedMinX = max(0, minX - padding)
        let paddedMinY = max(0, minY - padding)
        let paddedMaxX = min(1, maxX + padding)
        let paddedMaxY = min(1, maxY + padding)

        return NormalizedRect(
            x: paddedMinX, y: paddedMinY,
            width: paddedMaxX - paddedMinX, height: paddedMaxY - paddedMinY
        )
    }

    /// Shifts the framing target so the player has lead room in their
    /// direction of travel — moving right places the player left of
    /// center, and vice versa (spec section 15). `velocity.x` in
    /// normalized units/second; a near-zero velocity leaves the frame
    /// centered rather than jittering the offset for a stationary player.
    public static func applyLeadRoom(
        to target: NormalizedRect,
        playerVelocity: NormalizedPoint,
        maxOffsetFraction: Double = 0.15
    ) -> NormalizedRect {
        let speed = abs(playerVelocity.x)
        guard speed > 0.02 else { return target }

        // Saturating at a modest reference speed keeps lead room from
        // growing unbounded during a full sprint — beyond this speed,
        // more offset stops helping and starts pushing the player too
        // close to the frame edge.
        let referenceSpeed = 0.5
        let magnitude = min(speed / referenceSpeed, 1.0) * maxOffsetFraction
        let direction = playerVelocity.x > 0 ? 1.0 : -1.0
        // Moving right (+x): shift the CROP window right, which visually
        // places the player left-of-center, giving them room ahead.
        let shiftedX = target.x + direction * magnitude * target.width

        return NormalizedRect(
            x: min(max(shiftedX, 0), 1 - target.width),
            y: target.y, width: target.width, height: target.height
        )
    }

    /// Dead-zone + damped correction (spec section 11): if the target
    /// center is already within `deadZoneRadius` of true center, no
    /// motor correction at all. Otherwise, move only `dampingFactor` of
    /// the way toward the target this tick — never a full instantaneous
    /// jump — which is what produces smooth motion instead of jerky
    /// snapping/oscillation.
    public static func dampedCorrection(
        currentGimbalCenter: NormalizedPoint,
        targetCenter: NormalizedPoint,
        deadZoneRadius: Double = 0.06,
        dampingFactor: Double = 0.35
    ) -> NormalizedPoint? {
        let distance = currentGimbalCenter.distance(to: targetCenter)
        guard distance > deadZoneRadius else { return nil }

        return NormalizedPoint(
            x: currentGimbalCenter.x + (targetCenter.x - currentGimbalCenter.x) * dampingFactor,
            y: currentGimbalCenter.y + (targetCenter.y - currentGimbalCenter.y) * dampingFactor
        )
    }

    /// Zoom-safe minimum framing width/height so feet+ball always stay
    /// in frame during dribbling (spec section 15/16) — clamps a
    /// proposed framing box to never be narrower/shorter than this
    /// floor, expanding around its own center if needed.
    public static func enforceMinimumFrameSize(
        _ target: NormalizedRect,
        minWidth: Double = 0.35,
        minHeight: Double = 0.45
    ) -> NormalizedRect {
        guard target.width < minWidth || target.height < minHeight else { return target }
        let center = target.center
        let width = max(target.width, minWidth)
        let height = max(target.height, minHeight)
        return NormalizedRect(
            x: min(max(center.x - width / 2, 0), 1 - width),
            y: min(max(center.y - height / 2, 0), 1 - height),
            width: width, height: height
        )
    }
}
