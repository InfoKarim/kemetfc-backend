//
//  PoseAssociationTests.swift
//  KemetFCTrackerTests
//
//  Pose must belong to the coach-selected player: "no pose is better than the
//  wrong pose". Deterministic geometry only (no images, no faces).
//

import XCTest
@testable import KemetFCTracker

final class PoseAssociationTests: XCTestCase {
    private let jointNames = [
        "leftShoulder", "rightShoulder", "leftElbow", "rightElbow", "leftWrist", "rightWrist",
        "leftHip", "rightHip", "leftKnee", "rightKnee", "leftAnkle", "rightAnkle",
    ]

    /// A 12-joint body laid out inside `box`, horizontally shifted by `dx` (to build poses that
    /// drift out of / between boxes).
    private func pose(in box: NormalizedRect, dx: Double = 0) -> [PoseKeypoint] {
        jointNames.enumerated().map { index, name in
            let column = Double(index % 2)                                   // left / right side of the body
            let row = Double(index / 2) / 5.0                                // shoulders (0) ... ankles (1)
            return PoseKeypoint(
                name: name,
                x: box.x + box.width * (0.3 + 0.4 * column) + dx,
                y: box.y + box.height * (0.1 + 0.8 * row),
                confidence: 0.9
            )
        }
    }

    private let playerA = NormalizedRect(x: 0.15, y: 0.30, width: 0.12, height: 0.40)
    private let playerB = NormalizedRect(x: 0.60, y: 0.30, width: 0.12, height: 0.40)

    private func associate(_ poses: [[PoseKeypoint]], target: NormalizedRect? = nil, observed: Bool = true, people: [NormalizedRect]? = nil) -> PoseAssociationResult {
        PoseAssociator.associate(
            poses: poses, target: target ?? playerA, targetIsObserved: observed, otherPeople: people ?? [playerA, playerB]
        )
    }

    func testOnePlayerOnePoseIsMatched() {
        let mine = pose(in: playerA)
        let result = associate([mine], people: [playerA])
        XCTAssertEqual(result.outcome, .matched)
        XCTAssertEqual(result.keypoints, mine)
    }

    func testTwoPlayersTheSelectedPlayersOwnPoseIsChosen() {
        let a = pose(in: playerA), b = pose(in: playerB)
        let result = associate([a, b])
        XCTAssertEqual(result.outcome, .matched)
        XCTAssertEqual(result.keypoints, a, "must be A's pose, not B's")
    }

    func testSelectedPlayerIsNotTheFirstDetection() {
        // The old code took `results.first`. Here the FIRST pose is the other child's.
        let a = pose(in: playerA), b = pose(in: playerB)
        let result = associate([b, a])
        XCTAssertEqual(result.outcome, .matched)
        XCTAssertEqual(result.keypoints, a)
        XCTAssertNotEqual(result.keypoints, b)
    }

    func testOnlyAnotherChildsPoseMeansNoPose() {
        let result = associate([pose(in: playerB)])
        XCTAssertEqual(result.outcome, .foreign)
        XCTAssertNil(result.keypoints, "never attach another child's pose to the selected player")
    }

    func testOverlappingPlayersYieldNoPoseInsteadOfAGuess() {
        // Two children standing so close that BOTH bodies sit inside BOTH boxes.
        let a = NormalizedRect(x: 0.40, y: 0.30, width: 0.12, height: 0.40)
        let b = NormalizedRect(x: 0.45, y: 0.30, width: 0.12, height: 0.40)
        let result = associate([pose(in: a, dx: 0.02), pose(in: b, dx: -0.02)], target: a, people: [a, b])
        XCTAssertEqual(result.outcome, .ambiguous)
        XCTAssertNil(result.keypoints)
    }

    func testAPoseThatAnotherPersonsBoxExplainsBetterIsNotTheTargets() {
        // B stands half inside A's box. B's body: 8 joints in the zone both boxes cover, 4 only in B's box.
        // 67% of it lies inside A's box (above the inside threshold) but 100% inside B's: it is B's pose.
        let a = NormalizedRect(x: 0.40, y: 0.30, width: 0.12, height: 0.40)
        let b = NormalizedRect(x: 0.47, y: 0.30, width: 0.12, height: 0.40)
        let bodyOfB: [PoseKeypoint] = jointNames.enumerated().map { index, name in
            PoseKeypoint(name: name, x: index < 8 ? 0.50 : 0.57, y: 0.35 + 0.03 * Double(index), confidence: 0.9)
        }
        let result = associate([bodyOfB], target: a, people: [a, b])
        XCTAssertNotEqual(result.outcome, .matched, "B's pose must never be attached to A")
        XCTAssertNil(result.keypoints)
    }

    func testPartiallyOverlappingPlayersStillMatchWhenTheBodiesAreClearlySeparate() {
        let a = NormalizedRect(x: 0.40, y: 0.30, width: 0.12, height: 0.40)
        let b = NormalizedRect(x: 0.47, y: 0.30, width: 0.12, height: 0.40)
        let mine = pose(in: a, dx: -0.03), theirs = pose(in: b, dx: 0.03)
        let result = associate([theirs, mine], target: a, people: [a, b])
        XCTAssertEqual(result.outcome, .matched)
        XCTAssertEqual(result.keypoints, mine)
    }

    func testSelectedPlayerTemporarilyMissingMeansNoPoseEvenIfPosesExist() {
        let a = pose(in: playerA)
        let noBox = PoseAssociator.associate(poses: [a], target: nil, targetIsObserved: true, otherPeople: [playerA])
        XCTAssertEqual(noBox.outcome, .noObservedTarget)
        XCTAssertNil(noBox.keypoints)
        // A box that exists but was NOT observed this frame (stale/predicted) is not evidence either.
        let stale = associate([a], target: playerA, observed: false)
        XCTAssertEqual(stale.outcome, .noObservedTarget)
        XCTAssertNil(stale.keypoints)
    }

    func testTwoPosesClaimingTheSamePlayerWithoutAClearWinnerIsAmbiguous() {
        let first = pose(in: playerA), second = pose(in: playerA)
        let result = associate([first, second], people: [playerA])
        XCTAssertEqual(result.outcome, .ambiguous)
        XCTAssertNil(result.keypoints)
    }

    func testTwoPosesInTheSameBoxButOneClearlyBetterCentredWins() {
        let good = pose(in: playerA)
        var partial = pose(in: playerA)
        for index in 6..<12 { partial[index].x = 0.9 }         // half of this body is far outside the box
        let result = associate([partial, good], people: [playerA])
        XCTAssertEqual(result.outcome, .matched)
        XCTAssertEqual(result.keypoints, good)
    }

    func testPoseWithTooFewJointsIsNeverAssociated() {
        let sparse = Array(pose(in: playerA).prefix(3))
        XCTAssertEqual(associate([sparse], people: [playerA]).outcome, .noPoses)
    }

    func testNoPosesInTheFrame() {
        XCTAssertEqual(associate([]).outcome, .noPoses)
    }

    func testTheTargetsOwnDetectionInThePersonListIsNotTreatedAsARival() {
        // people list contains a slightly different box for the SAME player (IoU >= 0.6).
        let duplicate = NormalizedRect(x: 0.16, y: 0.31, width: 0.12, height: 0.40)
        let mine = pose(in: playerA)
        let result = associate([mine], people: [playerA, duplicate])
        XCTAssertEqual(result.outcome, .matched)
    }

    // MARK: - Pose returns after a safe reacquisition (real PlayerTracker)

    @MainActor
    func testPoseIsWithheldWhileTheTargetIsLostAndReturnsAfterSafeReacquisition() {
        let kit = AppearanceSignature(torsoColor: (30, 90, 200), legsColor: (20, 20, 60))
        let tracker = PlayerTracker(targetTrackId: 1, initialBoundingBox: playerA, appearance: kit)
        let mine = pose(in: playerA)
        let stranger = pose(in: playerB)
        let dt = 1.0 / 15.0
        var time = 0.0

        func step(detections: [PersonDetection]) -> FrameTelemetry {
            time += dt
            tracker.update(detections: detections, appearanceSignatures: detections.map { _ in kit }, at: time)
            return FrameTelemetryAssembler.assemble(
                playerState: tracker.state, playerBox: tracker.currentBoundingBox, playerConfidence: tracker.currentConfidence,
                playerTargetTrackId: tracker.targetTrackId, playerMatchWasAmbiguous: tracker.lastMatchWasAmbiguous,
                ballBox: nil, ballConfidence: 0, ballTrackId: nil,
                poses: [stranger, mine], personBoxes: detections.map(\.boundingBox), mode: .playerLock
            )
        }
        let visible = [PersonDetection(boundingBox: playerA, confidence: 0.9), PersonDetection(boundingBox: playerB, confidence: 0.9)]

        // 1. Observed: A's own pose, not the first (the stranger's).
        let observed = step(detections: visible)
        XCTAssertEqual(observed.poseAssociation, .matched)
        XCTAssertEqual(observed.poseKeypoints, mine)

        // 2. Target missing (only B detected): no pose at all, although poses are in the frame.
        for _ in 0..<3 {
            let missing = step(detections: [PersonDetection(boundingBox: playerB, confidence: 0.9)])
            XCTAssertNotEqual(missing.trackingStatus, "locked")
            XCTAssertEqual(missing.poseAssociation, .noObservedTarget)
            XCTAssertTrue(missing.poseKeypoints.isEmpty, "no pose is better than the wrong pose")
            XCTAssertNil(missing.playerBox)
        }

        // 3. A returns; the tracker reacquires only after probation. Until it is observed again: still no pose.
        var firstPoseFrame: Int?
        for frame in 1...8 {
            let telemetry = step(detections: visible)
            if telemetry.poseAssociation == .matched, firstPoseFrame == nil {
                firstPoseFrame = frame
                XCTAssertEqual(telemetry.trackingStatus, "reacquired")
                XCTAssertEqual(telemetry.poseKeypoints, mine)
            } else if firstPoseFrame == nil {
                XCTAssertTrue(telemetry.poseKeypoints.isEmpty, "not yet safely reacquired: no pose")
            }
        }
        XCTAssertNotNil(firstPoseFrame, "the pose comes back once the target is safely reacquired")
        XCTAssertGreaterThan(firstPoseFrame ?? 0, 1, "reacquisition needs probation frames")
    }
}
