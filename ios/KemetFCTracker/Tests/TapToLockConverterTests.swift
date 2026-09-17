//
//  TapToLockConverterTests.swift
//  KemetFCTrackerTests
//
//  STATUS: GENUINELY VERIFIED — see FramingMathTests.swift's header for
//  the exact methodology (standalone `swiftc` executable, not `swift
//  test`). Result when last run: 12/12 checks passed, after catching
//  and fixing a wrong hand-calculated tolerance in the test itself (see
//  comment below) — the SAME pattern as FramingMathTests.swift: real
//  execution surfaces real mistakes a read-through would have missed.
//

import XCTest
@testable import KemetFCTracker

final class TapToLockConverterTests: XCTestCase {
    let viewSize = CGSize(width: 400, height: 800)
    let videoDimensions = CGSize(width: 1920, height: 1080)

    func testTapAtViewCenterMapsToCameraSpaceCenter() {
        guard let point = TapToLockConverter.normalizedCameraPoint(
            tapPoint: CGPoint(x: 200, y: 400), viewSize: viewSize, videoDimensions: videoDimensions
        ) else { return XCTFail("Expected a value") }
        XCTAssertEqual(point.x, 0.5, accuracy: 1e-3)
        XCTAssertEqual(point.y, 0.5, accuracy: 1e-3)
    }

    func testTapAtViewTopLeftAccountsForAspectFillCrop() {
        guard let point = TapToLockConverter.normalizedCameraPoint(
            tapPoint: .zero, viewSize: viewSize, videoDimensions: videoDimensions
        ) else { return XCTFail("Expected a value") }
        // Portrait view (400x800) showing landscape video (1920x1080):
        // aspect fill scales by height, cropping the sides — so the
        // view's left edge is NOT the video's left edge.
        XCTAssertGreaterThan(point.x, 0.05)
        // No vertical crop occurs in this orientation.
        XCTAssertEqual(point.y, 0.0, accuracy: 1e-3)
    }

    func testTopLeftAndBottomRightAreSymmetricAroundCenter() {
        guard let topLeft = TapToLockConverter.normalizedCameraPoint(tapPoint: .zero, viewSize: viewSize, videoDimensions: videoDimensions),
              let bottomRight = TapToLockConverter.normalizedCameraPoint(
                tapPoint: CGPoint(x: viewSize.width, y: viewSize.height), viewSize: viewSize, videoDimensions: videoDimensions
              ) else { return XCTFail("Expected values") }
        XCTAssertEqual(topLeft.x + bottomRight.x, 1.0, accuracy: 1e-3)
        XCTAssertEqual(topLeft.y + bottomRight.y, 1.0, accuracy: 1e-3)
    }

    func testReturnsNilForZeroViewSize() {
        XCTAssertNil(TapToLockConverter.normalizedCameraPoint(tapPoint: .zero, viewSize: .zero, videoDimensions: videoDimensions))
    }

    // MARK: - selectDetection

    private let personA = PersonDetection(boundingBox: NormalizedRect(x: 0.1, y: 0.1, width: 0.2, height: 0.4), confidence: 0.9)
    private let personB = PersonDetection(boundingBox: NormalizedRect(x: 0.6, y: 0.1, width: 0.2, height: 0.4), confidence: 0.9)

    func testTapInsidePersonABoxSelectsA() {
        let selected = TapToLockConverter.selectDetection(at: NormalizedPoint(x: 0.2, y: 0.3), among: [personA, personB])
        XCTAssertEqual(selected?.boundingBox, personA.boundingBox)
    }

    func testTapInsidePersonBBoxSelectsB() {
        let selected = TapToLockConverter.selectDetection(at: NormalizedPoint(x: 0.7, y: 0.3), among: [personA, personB])
        XCTAssertEqual(selected?.boundingBox, personB.boundingBox)
    }

    func testTapOnEmptySpaceFarFromEveryoneReturnsNil() {
        // This is the specific spec requirement: "Do not lock an
        // arbitrary person when the user taps empty space."
        let selected = TapToLockConverter.selectDetection(at: NormalizedPoint(x: 0.95, y: 0.95), among: [personA, personB])
        XCTAssertNil(selected)
    }

    func testTapJustOutsideBoxWithinCenterDistanceToleranceStillSelects() {
        // personA's box center is (0.2, 0.3); a tap at (0.35, 0.3) is
        // 0.05 outside the box's right edge but 0.15 from center — an
        // earlier version of this test picked a 0.1 tolerance for this
        // exact tap point and failed on real execution because it
        // conflated "just outside the edge" with "close to center";
        // fixed with consistent numbers.
        let selected = TapToLockConverter.selectDetection(
            at: NormalizedPoint(x: 0.35, y: 0.3), among: [personA, personB], maxDistanceIfOutsideBox: 0.16
        )
        XCTAssertEqual(selected?.boundingBox, personA.boundingBox)
    }

    func testSelectDetectionReturnsNilWithNoDetections() {
        XCTAssertNil(TapToLockConverter.selectDetection(at: NormalizedPoint(x: 0.5, y: 0.5), among: []))
    }
}
