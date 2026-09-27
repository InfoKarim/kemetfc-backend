//
//  TorsoOrientationExperimentTests.swift
//  KemetFCTrackerTests
//
//  EXPERIMENTAL — see TorsoOrientationExperiment.swift. Proves only the math:
//  a stable torso is not reported as "changed", a real rotation is, and
//  missing/low-confidence landmarks never produce a reading or a claimed
//  change. Does not validate the signal is USEFUL — that requires real
//  footage, per the capability audit.
//

#if DEBUG
import XCTest
@testable import KemetFCTracker

final class TorsoOrientationExperimentTests: XCTestCase {
    private func shoulder(_ name: String, x: Double, y: Double, confidence: Double = 0.9) -> PoseKeypoint {
        PoseKeypoint(name: name, x: x, y: y, confidence: confidence)
    }

    func testStableOrientationIsNotReportedAsChanged() {
        let keypoints = [shoulder("left_shoulder", x: 0.4, y: 0.4), shoulder("right_shoulder", x: 0.6, y: 0.4)]
        let first = TorsoOrientationExperiment.torsoOrientation(from: keypoints, at: 0)
        let second = TorsoOrientationExperiment.torsoOrientation(from: keypoints, at: 1)
        XCTAssertNotNil(first)
        XCTAssertNotNil(second)
        XCTAssertFalse(TorsoOrientationExperiment.isOrientationChange(from: first!, to: second!))
    }

    func testGenuineRotationIsReportedAsChanged() {
        let before = [shoulder("left_shoulder", x: 0.4, y: 0.4), shoulder("right_shoulder", x: 0.6, y: 0.4)]
        // Shoulders now nearly vertical relative to each other — a large rotation.
        let after = [shoulder("left_shoulder", x: 0.5, y: 0.3), shoulder("right_shoulder", x: 0.5, y: 0.6)]
        let reading1 = TorsoOrientationExperiment.torsoOrientation(from: before, at: 0)!
        let reading2 = TorsoOrientationExperiment.torsoOrientation(from: after, at: 1)!
        XCTAssertTrue(TorsoOrientationExperiment.isOrientationChange(from: reading1, to: reading2))
    }

    func testMissingLandmarksProduceNoReading() {
        let keypoints = [shoulder("left_shoulder", x: 0.4, y: 0.4)] // right shoulder missing
        XCTAssertNil(TorsoOrientationExperiment.torsoOrientation(from: keypoints, at: 0))
    }

    func testLowConfidenceLandmarksProduceNoReading() {
        let keypoints = [
            shoulder("left_shoulder", x: 0.4, y: 0.4, confidence: 0.2),
            shoulder("right_shoulder", x: 0.6, y: 0.4, confidence: 0.9)
        ]
        XCTAssertNil(TorsoOrientationExperiment.torsoOrientation(from: keypoints, at: 0), "one low-confidence shoulder must void the whole reading")
    }

    func testLowConfidenceReadingNeverReportsAChangeEvenIfAngleDiffers() {
        let confidentBefore = TorsoOrientationReading(timestamp: 0, angleRadians: 0, confidence: 0.9)
        let lowConfidenceAfter = TorsoOrientationReading(timestamp: 1, angleRadians: .pi / 2, confidence: 0.1)
        XCTAssertFalse(TorsoOrientationExperiment.isOrientationChange(from: confidentBefore, to: lowConfidenceAfter))
    }

    func testAngleWraparoundIsHandledCorrectly() {
        // +179 degrees and -179 degrees are only 2 degrees apart, not 358.
        let a = TorsoOrientationReading(timestamp: 0, angleRadians: 179 * .pi / 180, confidence: 0.9)
        let b = TorsoOrientationReading(timestamp: 1, angleRadians: -179 * .pi / 180, confidence: 0.9)
        XCTAssertFalse(TorsoOrientationExperiment.isOrientationChange(from: a, to: b), "2 degrees of real rotation must not be reported as a change")
    }
}
#endif
