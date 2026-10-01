//
//  PlayerLockStatusTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 12's PLAYER UX items 11-15 (2026-09-29 production
//  incident fix). Proves PlayerLockStatusText never claims "PLAYER
//  LOCKED" for any state other than genuinely `.locked`.
//

import XCTest
@testable import KemetFCTracker

final class PlayerLockStatusTests: XCTestCase {
    // MARK: 11 — lost/unlocked (never locked) → SELECT state

    func testNeverLockedShowsSelectPlayer() {
        let history = PlayerLockHistory()
        XCTAssertEqual(PlayerLockStatusText.text(for: .lost, history: history), "SELECT PLAYER TO TRACK")
    }

    func testLostAfterPriorLockShowsRelock() {
        var history = PlayerLockHistory()
        history.observe(.locked)
        history.observe(.lost)
        XCTAssertEqual(PlayerLockStatusText.text(for: .lost, history: history), "TAP PLAYER TO RELOCK")
    }

    // MARK: 12 — searching → searching state

    func testSearchingShowsSearchingForPlayer() {
        let history = PlayerLockHistory()
        XCTAssertEqual(PlayerLockStatusText.text(for: .searching, history: history), "SEARCHING FOR PLAYER")
    }

    func testTemporarilyLostShowsSearchingForPlayer() {
        var history = PlayerLockHistory()
        history.observe(.locked)
        XCTAssertEqual(PlayerLockStatusText.text(for: .temporarilyLost, history: history), "SEARCHING FOR PLAYER")
    }

    // MARK: 13 — locked → locked state

    func testLockedShowsPlayerLocked() {
        let history = PlayerLockHistory()
        XCTAssertEqual(PlayerLockStatusText.text(for: .locked, history: history), "PLAYER LOCKED")
    }

    // MARK: 14 — reacquired → correct (distinct) state

    func testReacquiredShowsReacquiring() {
        var history = PlayerLockHistory()
        history.observe(.locked)
        XCTAssertEqual(PlayerLockStatusText.text(for: .reacquired, history: history), "REACQUIRING")
        XCTAssertNotEqual(
            PlayerLockStatusText.text(for: .reacquired, history: history),
            PlayerLockStatusText.text(for: .locked, history: history),
            "reacquired must remain visually distinguishable from a fresh, already-settled lock"
        )
    }

    // MARK: 15 — no false locked state

    func testNoStateOtherThanLockedEverProducesPlayerLockedText() {
        var history = PlayerLockHistory()
        history.observe(.locked) // establish "has ever locked" so .lost maps to the relock copy, not the first-time copy
        for state: PlayerTrackState in [.temporarilyLost, .searching, .lost] {
            XCTAssertNotEqual(PlayerLockStatusText.text(for: state, history: history), "PLAYER LOCKED", "\(state) must never claim PLAYER LOCKED")
        }
    }

    func testNeverLockedHistoryNeverFalselyShowsRelock() {
        let history = PlayerLockHistory() // hasEverLocked == false
        XCTAssertNotEqual(PlayerLockStatusText.text(for: .lost, history: history), "TAP PLAYER TO RELOCK", "must not claim a prior lock that never happened")
    }

    // MARK: PlayerLockHistory itself

    func testHistoryLatchesPermanentlyOnceLocked() {
        var history = PlayerLockHistory()
        XCTAssertFalse(history.hasEverLocked)
        history.observe(.searching)
        XCTAssertFalse(history.hasEverLocked, "searching alone must not count as having locked")
        history.observe(.locked)
        XCTAssertTrue(history.hasEverLocked)
        history.observe(.lost)
        XCTAssertTrue(history.hasEverLocked, "losing the lock later must not erase the fact it once happened")
    }

    func testHistoryLatchesOnReacquiredToo() {
        var history = PlayerLockHistory()
        history.observe(.reacquired)
        XCTAssertTrue(history.hasEverLocked)
    }

    func testIsSettledOnlyForLockedAndReacquired() {
        XCTAssertTrue(PlayerLockStatusText.isSettled(.locked))
        XCTAssertTrue(PlayerLockStatusText.isSettled(.reacquired))
        for state: PlayerTrackState in [.lost, .searching, .temporarilyLost] {
            XCTAssertFalse(PlayerLockStatusText.isSettled(state), "\(state) must not be treated as settled")
        }
    }
}
