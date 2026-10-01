//
//  PlayerLockStatus.swift
//  KemetFCTracker
//
//  Pure, UI-layer-only player-lock status text (2026-09-29 production
//  incident fix — a real 15-minute match recorded with the player NEVER
//  locked, and the only on-screen signal was a single dim, unlabeled
//  status dot). This file does NOT touch PlayerTracker or PlayerDetector
//  — it only reads the SAME `PlayerTrackState` the tracker already
//  publishes and derives clearer copy from it.
//
//  `PlayerTrackState.lost` is reused by the tracker for TWO different
//  real situations — "never locked yet" and "was locked, then lost for
//  good" — which read identically from the enum alone. `PlayerLockHistory`
//  is the smallest possible additional state (one bool) to tell these
//  apart for display purposes only; it never feeds back into tracking.
//

import Foundation

/// Tracks, for ONE recording session, whether a lock has EVER been
/// achieved — purely to choose between "first-time" and "relock" copy
/// below. Never read by, or fed back into, PlayerTracker/TrackingCoordinator.
public struct PlayerLockHistory: Equatable, Sendable {
    public private(set) var hasEverLocked: Bool

    public init(hasEverLocked: Bool = false) {
        self.hasEverLocked = hasEverLocked
    }

    /// Call once per `playerState` change. `.locked`/`.reacquired` latch
    /// `hasEverLocked` permanently true for the rest of this history's
    /// lifetime — never reset back to false by a later `.lost`.
    public mutating func observe(_ state: PlayerTrackState) {
        if state == .locked || state == .reacquired {
            hasEverLocked = true
        }
    }
}

/// Maps the tracker's real, current state to short, unambiguous
/// production copy. Never claims "PLAYER LOCKED" for any state other
/// than `.locked`/`.reacquired` — see each case below.
public enum PlayerLockStatusText {
    public static func text(for state: PlayerTrackState, history: PlayerLockHistory) -> String {
        switch state {
        case .locked:
            return "PLAYER LOCKED"
        case .reacquired:
            return "REACQUIRING"
        case .searching, .temporarilyLost:
            return "SEARCHING FOR PLAYER"
        case .lost:
            return history.hasEverLocked ? "TAP PLAYER TO RELOCK" : "SELECT PLAYER TO TRACK"
        }
    }

    /// `true` for any state this phase's UX treats as "all good, no
    /// action needed from the coach" — used only to decide whether the
    /// status banner should visually de-emphasize itself, never to
    /// change what text is computed above.
    public static func isSettled(_ state: PlayerTrackState) -> Bool {
        state == .locked || state == .reacquired
    }
}
