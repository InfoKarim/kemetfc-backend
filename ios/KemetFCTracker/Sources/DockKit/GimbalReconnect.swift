//
//  GimbalReconnect.swift
//  KemetFCTracker
//
//  Bounded, cancellation-safe reconnect sequencing for the gimbal (used by
//  DockKitManager; Foundation-only so it is unit-testable off-device).
//
//  THE DEFECT THIS REPLACES: the old attemptReconnect() counted "attempts" but
//  each call only slept once and then flipped the state to disconnected, so
//  "3 reconnect attempts" was really one 2-second wait per undock event.
//
//  Reality of DockKit: there is no API to actively connect to an accessory.
//  Reconnection is PASSIVE — the accessory reappears through
//  accessoryStateChanges as `.docked`. An "attempt" here is therefore one
//  bounded wait window (with growing delays) during which we watch for that
//  event. The sequence:
//    * runs at most `attemptDelays.count` windows — no infinite loop;
//    * stops as soon as the gimbal is back (`.reconnected`);
//    * stops immediately if the task is cancelled (`.cancelled`) — a `.docked`
//      event, stopMonitoring() or a new undock cancels it;
//    * reports `.exhausted` only after every window passed without success.
//

import Foundation

public struct GimbalReconnectPolicy: Sendable, Equatable {
    /// One wait window per attempt, in seconds. Growing, because a bump is
    /// usually back within a moment and a removed phone is not.
    public var attemptDelays: [TimeInterval]

    public init(attemptDelays: [TimeInterval] = [2, 3, 5]) {
        self.attemptDelays = attemptDelays
    }

    public var maxAttempts: Int { attemptDelays.count }
}

public enum GimbalReconnectOutcome: Equatable, Sendable {
    case reconnected(attempt: Int)
    case exhausted(attempts: Int)
    case cancelled
}

public enum GimbalReconnect {
    /// - Parameters:
    ///   - isReconnected: true once the accessory is connected again.
    ///   - onAttempt: called at the start of each window (1-based) — state/logging hook.
    ///   - sleep: injectable so tests run instantly; production uses Task.sleep.
    public static func run(
        policy: GimbalReconnectPolicy = GimbalReconnectPolicy(),
        isReconnected: @Sendable () async -> Bool,
        onAttempt: @Sendable (Int) async -> Void = { _ in },
        sleep: @Sendable (TimeInterval) async throws -> Void = { seconds in
            try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
        }
    ) async -> GimbalReconnectOutcome {
        for (index, delay) in policy.attemptDelays.enumerated() {
            let attempt = index + 1
            if Task.isCancelled { return .cancelled }
            if await isReconnected() { return .reconnected(attempt: attempt) }
            await onAttempt(attempt)
            do { try await sleep(delay) } catch { return .cancelled }
            if Task.isCancelled { return .cancelled }
            if await isReconnected() { return .reconnected(attempt: attempt) }
        }
        return .exhausted(attempts: policy.maxAttempts)
    }
}
