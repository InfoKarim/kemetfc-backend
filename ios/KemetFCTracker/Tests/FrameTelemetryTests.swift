//
//  FrameTelemetryTests.swift
//  KemetFCTrackerTests
//
//  What one frame's telemetry may claim: measurements only when observed this
//  frame, the ball tracker's real track id, nil where the system does not know.
//

import XCTest
@testable import KemetFCTracker

final class FrameTelemetryTests: XCTestCase {
    private let box = NormalizedRect(x: 0.4, y: 0.3, width: 0.12, height: 0.4)
    private let ball = NormalizedRect(x: 0.6, y: 0.6, width: 0.03, height: 0.03)

    private func assemble(
        state: PlayerTrackState? = .locked, confidence: Double = 0.9, ambiguous: Bool = false,
        ballBox: NormalizedRect? = nil, ballConfidence: Double = 0, ballTrackId: Int? = nil,
        poses: [[PoseKeypoint]] = [], mode: TrackingMode = .smartSoccer
    ) -> FrameTelemetry {
        FrameTelemetryAssembler.assemble(
            playerState: state, playerBox: box, playerConfidence: confidence, playerTargetTrackId: 1,
            playerMatchWasAmbiguous: ambiguous, ballBox: ballBox, ballConfidence: ballConfidence, ballTrackId: ballTrackId,
            poses: poses, personBoxes: [box], mode: mode
        )
    }

    func testObservedPlayerCarriesItsMeasurementAndTrackId() {
        let frame = assemble()
        XCTAssertEqual(frame.playerBox, box)
        XCTAssertEqual(frame.playerConfidence, 0.9)
        XCTAssertEqual(frame.playerTrackId, 1)
        XCTAssertEqual(frame.trackingStatus, "locked")
    }

    func testReacquiredPlayerIsAlsoObserved() {
        XCTAssertNotNil(assemble(state: .reacquired).playerBox)
        XCTAssertEqual(assemble(state: .reacquired).trackingStatus, "reacquired")
    }

    func testMissingPlayerHasNoStaleBoxConfidenceOrTrackId() {
        // The tracker still holds its LAST box while temporarily lost/searching; that is not a measurement.
        for state in [PlayerTrackState.temporarilyLost, .searching, .lost] {
            let frame = assemble(state: state, confidence: 0)
            XCTAssertNil(frame.playerBox, "\(state)")
            XCTAssertNil(frame.playerConfidence)
            XCTAssertNil(frame.playerTrackId)
            XCTAssertEqual(frame.trackingStatus, state.rawValue, "the status says what actually happened")
        }
        // A 'locked' tracker whose confidence was zeroed by a miss is not an observation either.
        XCTAssertNil(assemble(state: .locked, confidence: 0).playerBox)
    }

    func testBallIdIsTheTrackersRealIdentityAndNilWhenNotDetected() {
        let detected = assemble(ballBox: ball, ballConfidence: 0.35, ballTrackId: 7)
        XCTAssertEqual(detected.ballTrackId, 7, "not a hard-coded 1")
        XCTAssertEqual(detected.ballBox, ball)
        XCTAssertEqual(detected.ballConfidence, 0.35, "detector confidence preserved as reported")

        let notDetected = assemble(ballBox: nil, ballConfidence: 0, ballTrackId: nil)
        XCTAssertNil(notDetected.ballBox)
        XCTAssertNil(notDetected.ballConfidence)
        XCTAssertNil(notDetected.ballTrackId)
        // A box without a positive confidence is not a detection.
        XCTAssertNil(assemble(ballBox: ball, ballConfidence: 0, ballTrackId: 3).ballBox)
    }

    func testIdentityAmbiguityIsReportedAndUnknownWithoutATracker() {
        XCTAssertEqual(assemble(ambiguous: true).identityAmbiguous, true)
        XCTAssertEqual(assemble(ambiguous: false).identityAmbiguous, false)
        XCTAssertNil(assemble(state: nil).identityAmbiguous)
        XCTAssertEqual(assemble(state: nil).trackingStatus, "lost")
    }

    func testTrackingModeIsReportedVerbatim() {
        XCTAssertEqual(assemble(mode: .playerLock).trackingMode, "player_lock")
        XCTAssertEqual(assemble(mode: .ballTrack).trackingMode, "ball_track")
    }

    func testPoseIsEmptyWhenNothingIsAssociated() {
        XCTAssertTrue(assemble(poses: []).poseKeypoints.isEmpty)
        XCTAssertEqual(assemble(poses: []).poseAssociation, .noPoses)
    }
}
