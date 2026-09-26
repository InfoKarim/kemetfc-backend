//
//  FramingPipelineTests.swift
//  KemetFCTrackerTests
//
//  The production framing path (TrackingCoordinator delegates to FramingPipeline):
//  dampedCorrection is applied there, the player anchors the frame, and a ball
//  cannot pull the camera away.
//

import XCTest
@testable import KemetFCTracker

final class FramingPipelineTests: XCTestCase {
    private let still = NormalizedPoint(x: 0, y: 0)

    private func person(cx: Double, cy: Double = 0.5) -> NormalizedRect {
        NormalizedRect(x: cx - 0.06, y: cy - 0.2, width: 0.12, height: 0.4)
    }
    private func ball(cx: Double, cy: Double = 0.6) -> NormalizedRect {
        NormalizedRect(x: cx - 0.015, y: cy - 0.015, width: 0.03, height: 0.03)
    }

    private func next(_ pipeline: inout FramingPipeline, mode: TrackingMode = .playerLock, player: NormalizedRect?, observed: Bool = true,
                      ball: NormalizedRect? = nil, ballConfidence: Double = 0.4, velocity: NormalizedPoint? = nil) -> NormalizedRect? {
        pipeline.nextTarget(mode: mode, playerBox: player, playerObserved: observed, ballBox: ball,
                            ballConfidence: ballConfidence, playerVelocity: velocity ?? still)
    }

    // MARK: dampedCorrection is in the production path

    func testDeadZoneIgnoresSmallJitterSoTheGimbalIsNotChatteringOnDetectionNoise() {
        var pipeline = FramingPipeline()
        let first = next(&pipeline, player: person(cx: 0.5))!
        for offset in [0.02, -0.03, 0.025, -0.02, 0.04] {        // all inside the 0.06 dead zone of the calculator
            let target = next(&pipeline, player: person(cx: 0.5 + offset))!
            XCTAssertEqual(target.center.x, first.center.x, accuracy: 1e-9, "inside the dead zone the framing does not move")
        }
    }

    func testLargeMoveIsDampedNotFollowedInOneJump() {
        var pipeline = FramingPipeline()
        _ = next(&pipeline, player: person(cx: 0.3))
        let target = next(&pipeline, player: person(cx: 0.9))!
        let expectedPartial = FramingCalculator.dampedCorrection(
            currentGimbalCenter: NormalizedPoint(x: 0.3, y: 0.5), targetCenter: NormalizedPoint(x: 0.9, y: 0.5)
        )!
        XCTAssertLessThan(target.center.x, 0.9, "damped: it moves only part of the way")
        XCTAssertGreaterThan(target.center.x, 0.3)
        // Never farther than the calculator's own damped step, and never more than one bounded step.
        XCTAssertLessThanOrEqual(target.center.x, expectedPartial.x + 1e-9)
        XCTAssertLessThanOrEqual(abs(target.center.x - 0.3), pipeline.stabilizer.maxCenterStep + 1e-9)
    }

    func testTheTargetConvergesOnAPlayerWhoKeepsMoving() {
        var pipeline = FramingPipeline()
        _ = next(&pipeline, player: person(cx: 0.3))
        var last = 0.3
        for _ in 0..<30 {
            let center = next(&pipeline, player: person(cx: 0.8))!.center.x
            XCTAssertGreaterThanOrEqual(center, last - 1e-9)      // monotone, no oscillation
            last = center
        }
        XCTAssertEqual(last, 0.8, accuracy: 0.07, "settles inside the dead zone of the player")
    }

    func testNoPerUpdateJumpExceedsTheBoundedStep() {
        var pipeline = FramingPipeline()
        var previous = next(&pipeline, player: person(cx: 0.1))!.center
        for cx in [0.95, 0.05, 0.9, 0.1] {
            let center = next(&pipeline, player: person(cx: cx))!.center
            XCTAssertLessThanOrEqual(previous.distance(to: center), pipeline.stabilizer.maxCenterStep + 1e-9)
            previous = center
        }
    }

    // MARK: player anchors the frame

    func testUnobservedPlayerHoldsTheLastFramingAndABallCannotPullTheCamera() {
        var pipeline = FramingPipeline()
        let held = next(&pipeline, mode: .smartSoccer, player: person(cx: 0.5), ball: ball(cx: 0.55))!
        // The tracker lost the player; the ball is now far to the right and very visible.
        let whileLost = next(&pipeline, mode: .smartSoccer, player: person(cx: 0.5), observed: false, ball: ball(cx: 0.95), ballConfidence: 0.4)
        XCTAssertEqual(whileLost, held, "hold, never chase the ball")
    }

    func testBallLossNeverDropsThePlayer() {
        var pipeline = FramingPipeline()
        _ = next(&pipeline, mode: .smartSoccer, player: person(cx: 0.5), ball: ball(cx: 0.6))
        let withoutBall = next(&pipeline, mode: .smartSoccer, player: person(cx: 0.5), ball: nil)!
        XCTAssertTrue(withoutBall.x <= 0.5 - 0.06 + 0.2 && withoutBall.x + withoutBall.width >= 0.5, "the player is still inside the frame")
    }

    func testAFarAwayOrUnsureBallDoesNotDragTheFraming() {
        var reference = FramingPipeline()
        let playerOnly = next(&reference, mode: .smartSoccer, player: person(cx: 0.3))!

        var farBall = FramingPipeline()
        XCTAssertEqual(next(&farBall, mode: .smartSoccer, player: person(cx: 0.3), ball: ball(cx: 0.95)), playerOnly, "a ball across the pitch is ignored")

        var unsureBall = FramingPipeline()
        XCTAssertEqual(next(&unsureBall, mode: .smartSoccer, player: person(cx: 0.3), ball: ball(cx: 0.4), ballConfidence: 0.1), playerOnly, "a low-confidence ball is ignored")
    }

    func testANearbyConfidentBallExtendsTheFrameTowardIt() {
        var withBall = FramingPipeline()
        var withoutBall = FramingPipeline()
        let combined = next(&withBall, mode: .smartSoccer, player: person(cx: 0.3), ball: ball(cx: 0.5))!
        let playerOnly = next(&withoutBall, mode: .smartSoccer, player: person(cx: 0.3))!
        XCTAssertGreaterThan(combined.x + combined.width, playerOnly.x + playerOnly.width)
    }

    func testPlayerLockIgnoresTheBallCompletely() {
        var a = FramingPipeline(), b = FramingPipeline()
        XCTAssertEqual(next(&a, mode: .playerLock, player: person(cx: 0.4), ball: ball(cx: 0.5)), next(&b, mode: .playerLock, player: person(cx: 0.4)))
    }

    func testNothingIsInventedWhenNoFramingWasEverEstablished() {
        var pipeline = FramingPipeline()
        XCTAssertNil(next(&pipeline, player: nil, observed: false))
        XCTAssertNil(next(&pipeline, mode: .smartSoccer, player: person(cx: 0.5), observed: false, ball: ball(cx: 0.5)))
    }

    func testResetForgetsTheHeldFraming() {
        var pipeline = FramingPipeline()
        _ = next(&pipeline, player: person(cx: 0.5))
        pipeline.reset()
        XCTAssertNil(next(&pipeline, player: person(cx: 0.5), observed: false))
    }

    func testMinimumFrameSizeAndLeadRoomStillApply() {
        var pipeline = FramingPipeline()
        let target = next(&pipeline, player: person(cx: 0.5), velocity: NormalizedPoint(x: 0.6, y: 0))!
        XCTAssertGreaterThanOrEqual(target.width, 0.35 - 1e-9)
        XCTAssertGreaterThanOrEqual(target.height, 0.45 - 1e-9)
    }
}
