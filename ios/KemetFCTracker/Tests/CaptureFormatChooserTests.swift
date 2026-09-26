//
//  CaptureFormatChooserTests.swift
//  KemetFCTrackerTests
//
//  The camera format must not be a 240 fps slow-motion format when a plain 1080p@60
//  format exists (recordings from that setup contained ~30 frames).
//

import XCTest
@testable import KemetFCTracker

final class CaptureFormatChooserTests: XCTestCase {
    private func f(_ w: Int, _ h: Int, _ fps: Double) -> CaptureFormatInfo { CaptureFormatInfo(width: w, height: h, maxFrameRate: fps) }

    func testAPlain1080p60FormatBeatsAHighFrameRateOne() {
        // The old rule (highest max fps first) picked index 1 here.
        let formats = [f(1920, 1080, 30), f(1920, 1080, 240), f(1920, 1080, 60), f(3840, 2160, 60)]
        let choice = CaptureFormatChooser.choose(from: formats)
        XCTAssertEqual(choice?.index, 2)
        XCTAssertEqual(choice?.frameRate, 60)
        XCTAssertEqual(choice?.isHighFrameRateFormat, false)
    }

    func testTheSmallestPlain60FormatIsPreferredOver4K() {
        let formats = [f(3840, 2160, 60), f(2560, 1440, 60)]
        XCTAssertEqual(CaptureFormatChooser.choose(from: formats)?.index, 1)
    }

    func testWithoutAnyPlain60FormatTheFastestNonHighFrameRateFormatIsUsed() {
        let formats = [f(1920, 1080, 24), f(1920, 1080, 30), f(3840, 2160, 30)]
        let choice = CaptureFormatChooser.choose(from: formats)
        XCTAssertEqual(choice?.index, 1, "30 fps, smallest picture")
        XCTAssertEqual(choice?.frameRate, 30)
    }

    func testAHighFrameRateFormatIsOnlyTheLastResort() {
        let formats = [f(1920, 1080, 240), f(3840, 2160, 120), f(1920, 1080, 120)]
        let choice = CaptureFormatChooser.choose(from: formats)
        XCTAssertEqual(choice?.index, 2, "lowest top rate, then smallest picture")
        XCTAssertEqual(choice?.isHighFrameRateFormat, true)
        XCTAssertEqual(choice?.frameRate, 60, "still run at 60 fps")
    }

    func testFormatsBelow1080pAreNeverChosen() {
        XCTAssertNil(CaptureFormatChooser.choose(from: [f(1280, 720, 60), f(1920, 1080 - 1, 60)]))
        XCTAssertNil(CaptureFormatChooser.choose(from: []))
    }

    func testTheChoiceIsDeterministicForTies() {
        let formats = [f(1920, 1080, 60), f(1920, 1080, 60)]
        XCTAssertEqual(CaptureFormatChooser.choose(from: formats)?.index, 0)
    }
}
