//
//  BallBlobDetectorTests.swift
//  KemetFCTrackerTests
//
//  STATUS: GENUINELY VERIFIED — see FramingMathTests.swift's header for
//  the exact methodology (standalone `swiftc` executable, not `swift
//  test`). Result when last run: 8/8 checks passed on first run against
//  synthetic RGBA pixel buffers (no bugs found this time, unlike
//  PlayerTracker/TapToLockConverter — see those files' headers).
//

import XCTest
@testable import KemetFCTracker

final class BallBlobDetectorTests: XCTestCase {
    private let grass: (r: UInt8, g: UInt8, b: UInt8) = (40, 120, 40)
    private let ballWhite: (r: UInt8, g: UInt8, b: UInt8) = (235, 232, 225)
    private let redJersey: (r: UInt8, g: UInt8, b: UInt8) = (210, 30, 30)
    private let chalkLine: (r: UInt8, g: UInt8, b: UInt8) = (240, 240, 240)

    private func makeBuffer(width: Int, height: Int, fill: (r: UInt8, g: UInt8, b: UInt8)) -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        for i in stride(from: 0, to: bytes.count, by: 4) {
            bytes[i] = fill.r
            bytes[i + 1] = fill.g
            bytes[i + 2] = fill.b
            bytes[i + 3] = 255
        }
        return bytes
    }

    private func setPixel(_ bytes: inout [UInt8], width: Int, x: Int, y: Int, color: (r: UInt8, g: UInt8, b: UInt8)) {
        let i = (y * width + x) * 4
        bytes[i] = color.r
        bytes[i + 1] = color.g
        bytes[i + 2] = color.b
        bytes[i + 3] = 255
    }

    private func drawFilledCircle(_ bytes: inout [UInt8], width: Int, height: Int, cx: Int, cy: Int, radius: Int, color: (r: UInt8, g: UInt8, b: UInt8)) {
        for y in max(0, cy - radius)...min(height - 1, cy + radius) {
            for x in max(0, cx - radius)...min(width - 1, cx + radius) {
                let dx = x - cx
                let dy = y - cy
                if dx * dx + dy * dy <= radius * radius {
                    setPixel(&bytes, width: width, x: x, y: y, color: color)
                }
            }
        }
    }

    private func drawRect(_ bytes: inout [UInt8], width: Int, x0: Int, y0: Int, w: Int, h: Int, color: (r: UInt8, g: UInt8, b: UInt8)) {
        for y in y0..<(y0 + h) {
            for x in x0..<(x0 + w) {
                setPixel(&bytes, width: width, x: x, y: y, color: color)
            }
        }
    }

    func testBallSizedWhiteCircleOnGreenFieldIsFound() {
        let width = 200, height = 150
        var bytes = makeBuffer(width: width, height: height, fill: grass)
        drawFilledCircle(&bytes, width: width, height: height, cx: 100, cy: 75, radius: 8, color: ballWhite)
        let buffer = RGBAPixelBuffer(width: width, height: height, bytes: bytes)
        guard let box = BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer) else {
            return XCTFail("Expected a detection")
        }
        XCTAssertLessThan(abs(box.midX - 100), 3)
        XCTAssertLessThan(abs(box.midY - 75), 3)
        XCTAssertLessThanOrEqual(abs(box.width - box.height), 2)
    }

    func testUniformGrassFieldProducesNoDetection() {
        let width = 200, height = 150
        let bytes = makeBuffer(width: width, height: height, fill: grass)
        let buffer = RGBAPixelBuffer(width: width, height: height, bytes: bytes)
        XCTAssertNil(BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer))
    }

    func testColoredJerseyBlobIsRejectedByChannelBalanceGate() {
        let width = 200, height = 150
        var bytes = makeBuffer(width: width, height: height, fill: grass)
        drawFilledCircle(&bytes, width: width, height: height, cx: 100, cy: 75, radius: 8, color: redJersey)
        let buffer = RGBAPixelBuffer(width: width, height: height, bytes: bytes)
        XCTAssertNil(BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer))
    }

    func testElongatedChalkLineIsRejectedByAspectRatioGate() {
        let width = 200, height = 150
        var bytes = makeBuffer(width: width, height: height, fill: grass)
        drawRect(&bytes, width: width, x0: 40, y0: 74, w: 80, h: 3, color: chalkLine)
        let buffer = RGBAPixelBuffer(width: width, height: height, bytes: bytes)
        XCTAssertNil(BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer))
    }

    func testOversizedBrightPatchIsRejectedByAreaFractionGate() {
        let width = 200, height = 150
        var bytes = makeBuffer(width: width, height: height, fill: grass)
        drawRect(&bytes, width: width, x0: 10, y0: 10, w: 100, h: 100, color: ballWhite)
        let buffer = RGBAPixelBuffer(width: width, height: height, bytes: bytes)
        XCTAssertNil(BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer))
    }

    func testCircularBallPreferredOverThinLineElsewhereInFrame() {
        let width = 300, height = 200
        var bytes = makeBuffer(width: width, height: height, fill: grass)
        drawRect(&bytes, width: width, x0: 10, y0: 10, w: 60, h: 2, color: chalkLine)
        drawFilledCircle(&bytes, width: width, height: height, cx: 220, cy: 150, radius: 7, color: ballWhite)
        let buffer = RGBAPixelBuffer(width: width, height: height, bytes: bytes)
        guard let box = BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer) else {
            return XCTFail("Expected a detection")
        }
        XCTAssertLessThan(abs(box.midX - 220), 3)
        XCTAssertLessThan(abs(box.midY - 150), 3)
    }
}
