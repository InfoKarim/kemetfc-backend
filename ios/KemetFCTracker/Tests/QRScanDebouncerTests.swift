//
//  QRScanDebouncerTests.swift
//  KemetFCTrackerTests
//
//  Pure Foundation logic (no camera needed) backing the QR Scan tab's
//  duplicate-scan handling (spec: "Handle ... duplicate QR tokens
//  gracefully") and its client-side token-shape check.
//

import XCTest
@testable import KemetFCTracker

final class QRScanDebouncerTests: XCTestCase {
    func testFirstScanOfAValueAlwaysProcesses() {
        var debouncer = QRScanDebouncer()
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc"))
    }

    func testRepeatedScanOfSameValueWithinCooldownIsSuppressed() {
        var debouncer = QRScanDebouncer(cooldown: 3.0)
        let start = Date()
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc", at: start))
        XCTAssertFalse(debouncer.shouldProcess("KEMETCHK-abc", at: start.addingTimeInterval(0.5)))
        XCTAssertFalse(debouncer.shouldProcess("KEMETCHK-abc", at: start.addingTimeInterval(2.9)))
    }

    func testRepeatedScanOfSameValueAfterCooldownProcessesAgain() {
        var debouncer = QRScanDebouncer(cooldown: 3.0)
        let start = Date()
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc", at: start))
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc", at: start.addingTimeInterval(3.1)))
    }

    func testDifferentValueAlwaysProcessesImmediately() {
        var debouncer = QRScanDebouncer(cooldown: 3.0)
        let start = Date()
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc", at: start))
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-xyz", at: start.addingTimeInterval(0.01)))
    }

    func testResetAllowsImmediateReprocessingOfTheSameValue() {
        var debouncer = QRScanDebouncer(cooldown: 3.0)
        let start = Date()
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc", at: start))
        debouncer.reset()
        XCTAssertTrue(debouncer.shouldProcess("KEMETCHK-abc", at: start.addingTimeInterval(0.01)))
    }

    func testPlausibleTokenRequiresKemetPrefixAndContent() {
        XCTAssertTrue(KemetCheckInToken.isPlausibleToken("KEMETCHK-\(String(repeating: "a", count: 32))"))
        XCTAssertFalse(KemetCheckInToken.isPlausibleToken("KEMETCHK-"))
        XCTAssertFalse(KemetCheckInToken.isPlausibleToken("https://example.com"))
        XCTAssertFalse(KemetCheckInToken.isPlausibleToken(""))
    }
}
