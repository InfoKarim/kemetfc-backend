//
//  FramingMathTests.swift
//  KemetFCTrackerTests
//
//  STATUS: GENUINELY VERIFIED — unlike the rest of this module (see
//  TrackTypes.swift header), these exact assertions were compiled AND
//  EXECUTED with the real Swift 6.3.3 compiler on this development
//  machine (Command Line Tools, no full Xcode) against the real
//  TrackTypes.swift + FramingCalculator.swift source, via a standalone
//  `swiftc` invocation (SwiftPM's manifest linking is broken under
//  Command Line Tools alone in this environment, so this bypassed
//  `swift test` and compiled/ran a plain executable instead — see
//  ios/KemetFCTracker/README.md for the transcript and exactly what
//  this does and does not prove: it proves this pure-Foundation math is
//  correct on this machine's Swift toolchain; it does NOT prove this
//  XCTest file itself runs correctly inside Xcode/XCTest, since XCTest
//  requires the full Xcode toolchain this environment doesn't have.
//  Result when last run: 20/20 checks passed, including catching and
//  fixing TWO wrong assumptions in the test itself (see comments below)
//  — real execution surfaced real mistakes that a read-through alone
//  would have missed.
//

import XCTest
@testable import KemetFCTracker

final class FramingMathTests: XCTestCase {
    // MARK: - NormalizedRect / NormalizedPoint

    func testIoUOfIdenticalBoxesIsOne() {
        let rect = NormalizedRect(x: 0.1, y: 0.1, width: 0.2, height: 0.2)
        XCTAssertEqual(rect.iou(with: rect), 1.0, accuracy: 1e-6)
    }

    func testIoUOfNonOverlappingBoxesIsZero() {
        let a = NormalizedRect(x: 0.1, y: 0.1, width: 0.2, height: 0.2)
        let b = NormalizedRect(x: 0.8, y: 0.8, width: 0.1, height: 0.1)
        XCTAssertEqual(a.iou(with: b), 0.0, accuracy: 1e-6)
    }

    func testIoUOfPartiallyOverlappingBoxesIsBetweenZeroAndOne() {
        let a = NormalizedRect(x: 0.1, y: 0.1, width: 0.2, height: 0.2)
        let b = NormalizedRect(x: 0.2, y: 0.1, width: 0.2, height: 0.2)
        let iou = a.iou(with: b)
        XCTAssertGreaterThan(iou, 0)
        XCTAssertLessThan(iou, 1)
    }

    func testDistanceIsPythagorean() {
        let p1 = NormalizedPoint(x: 0.0, y: 0.0)
        let p2 = NormalizedPoint(x: 0.3, y: 0.4)
        XCTAssertEqual(p1.distance(to: p2), 0.5, accuracy: 1e-6)
    }

    // MARK: - combinedFramingTarget

    func testCombinedFramingTargetIncludesBothBoxesPlusPadding() {
        let player = NormalizedRect(x: 0.4, y: 0.3, width: 0.2, height: 0.5)
        let ball = NormalizedRect(x: 0.65, y: 0.7, width: 0.03, height: 0.03)
        guard let combined = FramingCalculator.combinedFramingTarget(player: player, ball: ball, padding: 0.05) else {
            return XCTFail("Expected a combined target")
        }
        XCTAssertEqual(combined.x, 0.35, accuracy: 1e-6)
        XCTAssertEqual(combined.x + combined.width, 0.73, accuracy: 1e-6)
        // The player's bottom edge (0.3 + 0.5 = 0.8) extends further
        // down than the ball's (0.7 + 0.03 = 0.73) in this fixture, so
        // the combined box's bottom is governed by the PLAYER, not the
        // ball — an early version of this test asserted the ball's edge
        // here and failed when actually run; fixed after real execution
        // caught the wrong hand-calculation.
        XCTAssertEqual(combined.y + combined.height, 0.85, accuracy: 1e-6)
    }

    func testCombinedFramingTargetReturnsNilWhenNeitherBoxPresent() {
        XCTAssertNil(FramingCalculator.combinedFramingTarget(player: nil, ball: nil))
    }

    func testCombinedFramingTargetWithOnlyPlayerApproximatelyEqualsPlayerBoxAtZeroPadding() {
        let player = NormalizedRect(x: 0.4, y: 0.3, width: 0.2, height: 0.5)
        guard let playerOnly = FramingCalculator.combinedFramingTarget(player: player, ball: nil, padding: 0.0) else {
            return XCTFail("Expected a combined target")
        }
        // Approximate, not exact equality: width/height are recomputed
        // as (max - min) rather than copied directly, so binary
        // floating-point rounding can differ from the source rect by a
        // few ULPs — exact `==` here failed on real execution even
        // though the values are correct to well beyond any meaningful
        // precision; this is expected floating-point behavior.
        XCTAssertEqual(playerOnly.x, player.x, accuracy: 1e-9)
        XCTAssertEqual(playerOnly.y, player.y, accuracy: 1e-9)
        XCTAssertEqual(playerOnly.width, player.width, accuracy: 1e-9)
        XCTAssertEqual(playerOnly.height, player.height, accuracy: 1e-9)
    }

    // MARK: - applyLeadRoom

    func testLeadRoomShiftsFrameRightWhenPlayerMovesRight() {
        let target = NormalizedRect(x: 0.4, y: 0.3, width: 0.2, height: 0.3)
        let movingRight = NormalizedPoint(x: 0.3, y: 0)
        let shifted = FramingCalculator.applyLeadRoom(to: target, playerVelocity: movingRight, maxOffsetFraction: 0.2)
        XCTAssertGreaterThan(shifted.x, target.x)
    }

    func testLeadRoomShiftsFrameLeftWhenPlayerMovesLeft() {
        let target = NormalizedRect(x: 0.4, y: 0.3, width: 0.2, height: 0.3)
        let movingLeft = NormalizedPoint(x: -0.3, y: 0)
        let shifted = FramingCalculator.applyLeadRoom(to: target, playerVelocity: movingLeft, maxOffsetFraction: 0.2)
        XCTAssertLessThan(shifted.x, target.x)
    }

    func testLeadRoomLeavesFrameUnchangedForNearlyStationaryPlayer() {
        let target = NormalizedRect(x: 0.4, y: 0.3, width: 0.2, height: 0.3)
        let stationary = NormalizedPoint(x: 0.001, y: 0)
        let unshifted = FramingCalculator.applyLeadRoom(to: target, playerVelocity: stationary)
        XCTAssertEqual(unshifted.x, target.x, accuracy: 1e-9)
    }

    // MARK: - dampedCorrection

    func testDampedCorrectionIsNilInsideDeadZone() {
        let centered = NormalizedPoint(x: 0.5, y: 0.5)
        let slightlyOff = NormalizedPoint(x: 0.52, y: 0.5)
        XCTAssertNil(FramingCalculator.dampedCorrection(currentGimbalCenter: centered, targetCenter: slightlyOff, deadZoneRadius: 0.06))
    }

    func testDampedCorrectionMovesOnlyPartwayTowardFarTarget() {
        let centered = NormalizedPoint(x: 0.5, y: 0.5)
        let farOff = NormalizedPoint(x: 0.8, y: 0.5)
        guard let correction = FramingCalculator.dampedCorrection(
            currentGimbalCenter: centered, targetCenter: farOff, deadZoneRadius: 0.06, dampingFactor: 0.35
        ) else {
            return XCTFail("Expected a correction for a target outside the dead zone")
        }
        XCTAssertGreaterThan(correction.x, 0.5)
        XCTAssertLessThan(correction.x, 0.8)
        XCTAssertEqual(correction.x, 0.5 + (0.8 - 0.5) * 0.35, accuracy: 1e-9)
    }

    // MARK: - enforceMinimumFrameSize

    func testEnforceMinimumFrameSizeWidensATooTightBox() {
        let tooTight = NormalizedRect(x: 0.45, y: 0.45, width: 0.1, height: 0.1)
        let enforced = FramingCalculator.enforceMinimumFrameSize(tooTight, minWidth: 0.35, minHeight: 0.45)
        XCTAssertGreaterThanOrEqual(enforced.width, 0.35)
        XCTAssertGreaterThanOrEqual(enforced.height, 0.45)
    }

    func testEnforceMinimumFrameSizeLeavesAnAlreadyWideBoxAlone() {
        let alreadyWide = NormalizedRect(x: 0.1, y: 0.1, width: 0.5, height: 0.6)
        let unchanged = FramingCalculator.enforceMinimumFrameSize(alreadyWide, minWidth: 0.35, minHeight: 0.45)
        XCTAssertEqual(unchanged, alreadyWide)
    }
}
