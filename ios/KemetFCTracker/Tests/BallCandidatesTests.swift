//
//  BallCandidatesTests.swift
//  KemetFCTrackerTests
//
//  The classical detector now exposes EVERY plausible candidate so the tracker
//  can associate them over time. Synthetic buffers only — this says nothing
//  about real-video accuracy.
//

import XCTest
@testable import KemetFCTracker

final class BallCandidatesTests: XCTestCase {
    private let grass: (UInt8, UInt8, UInt8) = (40, 120, 40)
    private let white: (UInt8, UInt8, UInt8) = (235, 232, 225)

    private func frame(width: Int = 120, height: Int = 80, discs: [(cx: Int, cy: Int, r: Int)]) -> RGBAPixelBuffer {
        var bytes = [UInt8](repeating: 255, count: width * height * 4)
        for y in 0..<height {
            for x in 0..<width {
                var color = grass
                for disc in discs where (x - disc.cx) * (x - disc.cx) + (y - disc.cy) * (y - disc.cy) <= disc.r * disc.r { color = white }
                let i = (y * width + x) * 4
                bytes[i] = color.0; bytes[i + 1] = color.1; bytes[i + 2] = color.2
            }
        }
        return RGBAPixelBuffer(width: width, height: height, bytes: bytes)
    }

    func testEveryCirclePassingTheGatesIsReturned() {
        let candidates = BallBlobDetector.findBrightRoughlyCircularBlobs(in: frame(discs: [(30, 40, 5), (90, 20, 5), (60, 65, 6)]))
        XCTAssertEqual(candidates.count, 3, "the detector no longer prematurely reduces the frame to one candidate")
    }

    func testTheSingleBlobApiStillReturnsTheBestCandidate() {
        let buffer = frame(discs: [(30, 40, 5), (90, 20, 6)])
        let best = BallBlobDetector.findBrightRoughlyCircularBlobs(in: buffer).first?.rect
        XCTAssertEqual(BallBlobDetector.findBrightRoughlyCircularBlob(in: buffer), best)
    }

    func testNoCandidateInAnEmptyFieldIsNotFabricated() {
        XCTAssertTrue(BallBlobDetector.findBrightRoughlyCircularBlobs(in: frame(discs: [])).isEmpty)
    }

    func testCandidateCountIsCapped() {
        let discs = (0..<8).map { (cx: 12 + 13 * $0, cy: 20 + (($0 % 2) * 30), r: 4) }
        XCTAssertLessThanOrEqual(BallBlobDetector.findBrightRoughlyCircularBlobs(in: frame(discs: discs), maxCandidates: 5).count, 5)
    }

    func testClassicalConfidenceStaysLowAndHonest() {
        for fill in stride(from: 0.55, through: 1.0, by: 0.05) {
            let candidate = BallBlobDetector.Candidate(rect: .zero, fillRatio: fill, aspectRatio: 1.0)
            let confidence = ClassicalCVBallDetector.confidence(for: candidate)
            XCTAssertGreaterThanOrEqual(confidence, 0.20)
            XCTAssertLessThanOrEqual(confidence, 0.40, "a heuristic never claims a trained detector's certainty")
        }
    }
}
