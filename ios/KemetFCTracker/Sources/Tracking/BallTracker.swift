//
//  BallTracker.swift
//  KemetFCTracker
//
//  STATUS: Type-checked with the real Swift 6.3.3 compiler on this
//  machine (`swiftc -typecheck`, zero errors, alongside TrackTypes.swift
//  and PlayerTracker.swift). NOT executed, not run on a device — see
//  PlayerTracker.swift header for the same caveat in full, and
//  TrackTypes.swift header for the module-wide caveat.
//
//  Ball track continuity: motion prediction across brief disappearances,
//  and a plausibility gate so a new detection is only accepted as "the
//  same ball" if it's within a physically reasonable distance of the
//  predicted position — this is what stops "immediately jump to a
//  different circular object" (spec section 8).
//

import Combine
import Foundation

@MainActor
public final class BallTracker: ObservableObject {
    @Published public private(set) var state: BallTrackState = .lost
    @Published public private(set) var currentBoundingBox: NormalizedRect?
    @Published public private(set) var currentConfidence: Double = 0

    public let trackId: Int
    private var lastKnownCenter: NormalizedPoint?
    private var lastKnownVelocity: NormalizedPoint = .init(x: 0, y: 0)
    private var lastUpdateTime: TimeInterval = 0
    private var lostSince: TimeInterval?

    /// A soccer ball moves far faster (relative to frame size) than a
    /// player — this gate is deliberately wider than PlayerTracker's,
    /// but still bounded, per spec section 8: "Do not immediately jump
    /// to a different circular object."
    private let maxPlausibleSpeedUnitsPerSecond = 2.5
    private let maxSearchSeconds: TimeInterval = 2.0

    public init(trackId: Int = 1) {
        self.trackId = trackId
    }

    public func update(detections: [BallDetection], at time: TimeInterval) {
        defer { lastUpdateTime = time }
        let dt = lastUpdateTime > 0 ? max(time - lastUpdateTime, 1.0 / 120.0) : 0
        let predicted = predictedCenter(dt: dt)

        var best: BallDetection?
        var bestDistance = Double.infinity

        for detection in detections {
            let distance = predicted.distance(to: detection.boundingBox.center)
            let maxPlausibleDistance = 0.03 + maxPlausibleSpeedUnitsPerSecond * dt
            guard distance <= maxPlausibleDistance else { continue }
            if distance < bestDistance {
                bestDistance = distance
                best = detection
            }
        }

        guard let best else {
            handleMiss(at: time)
            return
        }

        if let previous = lastKnownCenter, dt > 0 {
            let newCenter = best.boundingBox.center
            lastKnownVelocity = NormalizedPoint(
                x: (newCenter.x - previous.x) / dt,
                y: (newCenter.y - previous.y) / dt
            )
        }
        lastKnownCenter = best.boundingBox.center
        currentBoundingBox = best.boundingBox
        currentConfidence = best.confidence
        lostSince = nil
        state = .tracked
    }

    private func handleMiss(at time: TimeInterval) {
        currentConfidence = 0
        switch state {
        case .tracked:
            state = .searching
            lostSince = time
        case .searching:
            if let since = lostSince, time - since > maxSearchSeconds {
                state = .lost
                currentBoundingBox = nil
            }
        case .lost:
            break
        }
    }

    /// Used both for re-acquisition matching above and by
    /// TrackingCoordinator to keep reporting a "predicted" ball position
    /// for framing purposes during a brief .searching window, rather
    /// than snapping the frame wider the instant one frame misses.
    public func predictedCenter(dt: TimeInterval) -> NormalizedPoint {
        guard let center = lastKnownCenter else { return NormalizedPoint(x: 0.5, y: 0.5) }
        guard state == .searching else { return center }
        return NormalizedPoint(
            x: center.x + lastKnownVelocity.x * dt,
            y: center.y + lastKnownVelocity.y * dt
        )
    }
}
