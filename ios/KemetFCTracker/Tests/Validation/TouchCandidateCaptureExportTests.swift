//
//  TouchCandidateCaptureExportTests.swift
//  KemetFCTrackerTests
//
//  Proves TouchCandidateCaptureExport (Sources/Tracking/) and its offline
//  adapter never fabricate synchronization, never overwrite analysis time,
//  and round-trip deterministically. See the 2026-09-28 real-data capture
//  bridge report for which Part 15 items are covered here vs. documented as
//  architectural properties of not-yet-wired lifecycle code (items 15/16,
//  and 21, which is already covered by the existing evaluator test suite).
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateCaptureExportTests: XCTestCase {
    private func event(
        interactionTime: TimeInterval = 12.5, ballTrackId: Int? = 3,
        speedChange: Double? = 0.15, directionSimilarity: Double? = -0.2, minimumDistance: Double? = 0.8
    ) -> TouchCandidateEvent {
        let evidence = TouchCandidateEvidence(
            preBallSpeed: 0.2, postBallSpeed: 0.35, speedChange: speedChange,
            directionSimilarity: directionSimilarity, minimumPlayerBallDistance: minimumDistance,
            coMovementBefore: .notSupported, coMovementAfter: .candidate, preSampleCount: 5, postSampleCount: 5
        )
        return TouchCandidateEvent(startTime: interactionTime - 0.5, interactionTime: interactionTime, endTime: interactionTime + 0.5, ballTrackId: ballTrackId, evidence: evidence)
    }

    // MARK: 1-2 — serialization, empty predictions

    func testPredictionSerializationRoundTrips() throws {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: "S1", videoFilename: "clip.mov", videoDurationSeconds: 60.0,
            events: [event()], synchronizationAnchorSeconds: nil
        )
        let data = try TouchCandidateCaptureExportIO.encoder().encode(export)
        let decoded = try TouchCandidateCaptureExportIO.decoder().decode(TouchCandidateCaptureExport.self, from: data)
        XCTAssertEqual(decoded, export)
    }

    func testEmptyPredictionsSerializesAsValidNotAsFailure() throws {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "clip.mov", videoDurationSeconds: nil,
            events: [], synchronizationAnchorSeconds: nil
        )
        XCTAssertTrue(export.predictions.isEmpty)
        let data = try TouchCandidateCaptureExportIO.encoder().encode(export)
        let decoded = try TouchCandidateCaptureExportIO.decoder().decode(TouchCandidateCaptureExport.self, from: data)
        XCTAssertEqual(decoded.predictions, [], "an empty predictions array is valid data, not an export failure")
    }

    // MARK: 3-4 — identity, analysis timestamp preserved

    func testAssessmentAndSessionIdentityPreserved() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "ABC123", sessionId: "SESSION-9", videoFilename: "v.mov", videoDurationSeconds: 30,
            events: [], synchronizationAnchorSeconds: nil
        )
        XCTAssertEqual(export.assessmentId, "ABC123")
        XCTAssertEqual(export.sessionId, "SESSION-9")
    }

    func testAnalysisTimestampIsPreservedExactlyNotRecomputed() {
        let e = event(interactionTime: 42.125)
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [e], synchronizationAnchorSeconds: nil
        )
        XCTAssertEqual(export.predictions.first?.interactionTimeSeconds, e.interactionTime, "must be the SAME value as TouchCandidateEvent.interactionTime — never recomputed")
    }

    // MARK: 5-6 — video-relative timestamp never fabricated / unavailable is explicit

    func testVideoRelativeTimestampIsNeverFabricatedAtExportTime() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event()], synchronizationAnchorSeconds: 5.0
        )
        XCTAssertNil(export.synchronization?.videoOriginSeconds, "the video's own origin is only knowable by an offline step reading the finished .mov — never invented here")
        XCTAssertNil(export.synchronization?.offsetSeconds, "no real offset is known at export time either")
    }

    func testSynchronizationUnavailableIsRepresentedExplicitlyNotAsZeroOffset() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [], synchronizationAnchorSeconds: nil
        )
        XCTAssertNil(export.synchronization, "no anchor supplied → no synchronization record at all, never a fabricated zero-offset one")
    }

    // MARK: 7-9 — measured synchronization, schema version, determinism

    func testMeasuredSynchronizationRecordSerializesAndRoundTrips() throws {
        let sync = TimebaseSynchronizationRecord(
            assessmentId: "A1", analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
            capturePtsOriginSeconds: 1000.25, videoOriginSeconds: 1000.05,
            offsetSeconds: 0.20, provenance: .measuredFromCapturePTS, createdAt: Date(timeIntervalSince1970: 1_700_000_000)
        )
        let export = TouchCandidateCaptureExport(
            assessmentId: "A1", sessionId: "S1", video: VideoReference(filename: "v.mov", durationSeconds: 60),
            predictionTimebase: .analysisSeconds, synchronization: sync, predictions: []
        )
        let data = try TouchCandidateCaptureExportIO.encoder().encode(export)
        let decoded = try TouchCandidateCaptureExportIO.decoder().decode(TouchCandidateCaptureExport.self, from: data)
        XCTAssertEqual(decoded.synchronization, sync)
    }

    func testSchemaVersionDefaultsAndRoundTrips() throws {
        let export = TouchCandidateCaptureExport.build(assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil, events: [], synchronizationAnchorSeconds: nil)
        XCTAssertEqual(export.schemaVersion, TouchCandidateCaptureExport.currentSchemaVersion)
        let data = try TouchCandidateCaptureExportIO.encoder().encode(export)
        let decoded = try TouchCandidateCaptureExportIO.decoder().decode(TouchCandidateCaptureExport.self, from: data)
        XCTAssertEqual(decoded.schemaVersion, 1)
    }

    func testJSONEncodingIsDeterministic() throws {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: "S1", videoFilename: "v.mov", videoDurationSeconds: 60,
            events: [event(), event(interactionTime: 20.0)], synchronizationAnchorSeconds: nil
        )
        let data1 = try TouchCandidateCaptureExportIO.encoder().encode(export)
        let data2 = try TouchCandidateCaptureExportIO.encoder().encode(export)
        XCTAssertEqual(data1, data2)
    }

    // MARK: 10-13 — multiple predictions, optionals, isolation

    func testMultiplePredictionsPreserveOrderAndCount() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event(interactionTime: 1.0), event(interactionTime: 2.0), event(interactionTime: 3.0)],
            synchronizationAnchorSeconds: nil
        )
        XCTAssertEqual(export.predictions.map { $0.interactionTimeSeconds }, [1.0, 2.0, 3.0])
    }

    func testBallTrackIdNilIsHandledExplicitly() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event(ballTrackId: nil)], synchronizationAnchorSeconds: nil
        )
        XCTAssertNil(export.predictions.first?.ballTrackId)
    }

    func testOptionalEvidenceFieldsAllNilDoesNotCrashOrFabricate() throws {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event(speedChange: nil, directionSimilarity: nil, minimumDistance: nil)], synchronizationAnchorSeconds: nil
        )
        let p = export.predictions.first
        XCTAssertNil(p?.speedChange)
        XCTAssertNil(p?.directionSimilarity)
        XCTAssertNil(p?.minimumPlayerBallDistance)
        // Must still encode cleanly with nils present.
        _ = try TouchCandidateCaptureExportIO.encoder().encode(export)
    }

    func testDifferentAssessmentIdsRemainIsolated() {
        let exportA = TouchCandidateCaptureExport.build(assessmentId: "A1", sessionId: nil, videoFilename: "a.mov", videoDurationSeconds: nil, events: [event(interactionTime: 1.0)], synchronizationAnchorSeconds: nil)
        let exportB = TouchCandidateCaptureExport.build(assessmentId: "B1", sessionId: nil, videoFilename: "b.mov", videoDurationSeconds: nil, events: [event(interactionTime: 99.0)], synchronizationAnchorSeconds: nil)
        XCTAssertEqual(exportA.assessmentId, "A1")
        XCTAssertEqual(exportB.assessmentId, "B1")
        XCTAssertEqual(exportA.predictions.first?.interactionTimeSeconds, 1.0)
        XCTAssertEqual(exportB.predictions.first?.interactionTimeSeconds, 99.0)
    }

    /// Item 14 (re-lock behavior): `TrackingCoordinator.touchCandidateEvents`
    /// is already cleared on every `lockPlayer()` call (verified, unchanged
    /// this phase — see `testResetClearsInProgressEpisodeAndHistory` in
    /// `TouchCandidateDetectorTests.swift` for the detector-level guarantee,
    /// and `TrackingCoordinator.lockPlayer`'s own `touchCandidateEvents = []`
    /// line). `build(...)` itself is STATELESS — it has no memory between
    /// calls and reflects only whatever `events` array it is given — so
    /// feeding it a post-reset (smaller/different) array can never leak a
    /// prior epoch's predictions. This test proves the stateless half of
    /// that guarantee; the reset-clearing half is proven elsewhere.
    func testStatelessBuildNeverLeaksPriorCallsEvents() {
        let firstEpoch = TouchCandidateCaptureExport.build(assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil, events: [event(interactionTime: 1.0)], synchronizationAnchorSeconds: nil)
        let secondEpoch = TouchCandidateCaptureExport.build(assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil, events: [event(interactionTime: 99.0)], synchronizationAnchorSeconds: nil)
        XCTAssertEqual(firstEpoch.predictions.map { $0.interactionTimeSeconds }, [1.0])
        XCTAssertEqual(secondEpoch.predictions.map { $0.interactionTimeSeconds }, [99.0], "a fresh events array must never inherit a prior call's predictions")
    }

    // MARK: 17-19 — adapter / evaluator compatibility

    func testAdapterProducesAnEquivalentPredictionDataset() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event(interactionTime: 5.0)], synchronizationAnchorSeconds: nil
        )
        let dataset = TouchCandidateCaptureExportAdapter.predictionDataset(from: export)
        XCTAssertEqual(dataset.assessmentId, "A1")
        XCTAssertEqual(dataset.timebase, .analysisSeconds)
        XCTAssertEqual(dataset.predictions.first?.timeSeconds, 5.0)
    }

    func testTimestampMappingFlowsThroughToTheEvaluatorCorrectly() throws {
        let sync = TimebaseSynchronizationRecord(
            assessmentId: "A1", analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
            capturePtsOriginSeconds: 100.0, videoOriginSeconds: 99.8,
            offsetSeconds: 0.2, provenance: .measuredFromCapturePTS
        )
        let export = TouchCandidateCaptureExport(
            assessmentId: "A1", sessionId: nil, video: VideoReference(filename: "v.mov"),
            predictionTimebase: .analysisSeconds, synchronization: sync,
            predictions: [ExportedTouchCandidatePrediction(from: event(interactionTime: 9.8))]
        )
        let predictions = TouchCandidateCaptureExportAdapter.predictionDataset(from: export)
        guard let mapping = TouchCandidateCaptureExportAdapter.timestampMapping(from: export) else {
            return XCTFail("a measured synchronization record must produce a usable mapping")
        }
        XCTAssertEqual(mapping.provenance, .measuredPerSession)
        let groundTruth = GroundTruthDataset(assessmentId: "A1", timebase: .videoSeconds, touches: [GroundTruthTouch(id: "g1", timeSeconds: 10.0)])
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth, predictions: predictions, mapping: mapping)
        XCTAssertEqual(result.truePositiveCount, 1, "9.8 (analysis) + 0.2 offset = 10.0 (video) — must match the ground truth at 10.0")
    }

    func testZeroPredictionsThroughTheAdapterRemainsValid() throws {
        let export = TouchCandidateCaptureExport.build(assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil, events: [], synchronizationAnchorSeconds: nil)
        let predictions = TouchCandidateCaptureExportAdapter.predictionDataset(from: export)
        let groundTruth = GroundTruthDataset(assessmentId: "A1", timebase: .analysisSeconds, touches: [GroundTruthTouch(id: "g1", timeSeconds: 1.0)])
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth, predictions: predictions)
        XCTAssertEqual(result.predictionCount, 0)
        XCTAssertEqual(result.falseNegativeCount, 1)
    }

    // MARK: 20 — malformed synchronization rejected by the adapter

    func testMalformedSynchronizationClaimingMeasuredWithNoOffsetIsRejectedByTheAdapter() {
        let malformed = TimebaseSynchronizationRecord(
            assessmentId: "A1", analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
            capturePtsOriginSeconds: 100.0, videoOriginSeconds: nil,
            offsetSeconds: nil, // malformed: claims a measured provenance but has no actual offset
            provenance: .measuredFromCapturePTS
        )
        let export = TouchCandidateCaptureExport(
            assessmentId: "A1", sessionId: nil, video: VideoReference(filename: "v.mov"),
            predictionTimebase: .analysisSeconds, synchronization: malformed, predictions: []
        )
        XCTAssertNil(TouchCandidateCaptureExportAdapter.timestampMapping(from: export), "a provenance claim with no actual offset must never produce a usable mapping")
    }

    // MARK: 22-25 — robustness, provenance, sign

    func testNonFiniteTimestampsAreRejectedByEncodingNotSilentlyAccepted() {
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event(speedChange: .nan)], synchronizationAnchorSeconds: nil
        )
        XCTAssertThrowsError(try TouchCandidateCaptureExportIO.encoder().encode(export), "JSON has no representation for NaN/Infinity — encoding must throw, never silently emit invalid JSON")
    }

    func testNegativeTimestampsRoundTripAccordingToTheExplicitContract() throws {
        // No special-case rejection: a negative interactionTime is unusual
        // but not itself invalid (mirrors the evaluator's own contract).
        let export = TouchCandidateCaptureExport.build(
            assessmentId: "A1", sessionId: nil, videoFilename: "v.mov", videoDurationSeconds: nil,
            events: [event(interactionTime: -0.1)], synchronizationAnchorSeconds: nil
        )
        let data = try TouchCandidateCaptureExportIO.encoder().encode(export)
        let decoded = try TouchCandidateCaptureExportIO.decoder().decode(TouchCandidateCaptureExport.self, from: data)
        XCTAssertEqual(decoded.predictions.first?.interactionTimeSeconds, -0.1)
    }

    func testSynchronizationProvenanceIsPreservedNotCollapsed() throws {
        for provenance: SynchronizationProvenance in [.measuredFromCapturePTS, .derivedFromRecordedAsset, .approximateHistoricalOffset, .unavailable] {
            let sync = TimebaseSynchronizationRecord(
                assessmentId: "A1", analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
                capturePtsOriginSeconds: nil, offsetSeconds: provenance == .unavailable ? nil : 0.1, provenance: provenance
            )
            let export = TouchCandidateCaptureExport(assessmentId: "A1", sessionId: nil, video: VideoReference(filename: "v.mov"), predictionTimebase: .analysisSeconds, synchronization: sync, predictions: [])
            let data = try TouchCandidateCaptureExportIO.encoder().encode(export)
            let decoded = try TouchCandidateCaptureExportIO.decoder().decode(TouchCandidateCaptureExport.self, from: data)
            XCTAssertEqual(decoded.synchronization?.provenance, provenance)
        }
    }

    func testMappingSignConventionIsVideoEqualsAnalysisPlusOffset() throws {
        // A prediction at analysis-time 9.9, with a +0.1 offset, must land
        // at video-time 10.0 — never 9.8 (the opposite sign).
        let sync = TimebaseSynchronizationRecord(
            assessmentId: "A1", analysisTimebase: .analysisSeconds, videoTimebase: .videoSeconds,
            capturePtsOriginSeconds: nil, offsetSeconds: 0.1, provenance: .measuredFromCapturePTS
        )
        let export = TouchCandidateCaptureExport(
            assessmentId: "A1", sessionId: nil, video: VideoReference(filename: "v.mov"),
            predictionTimebase: .analysisSeconds, synchronization: sync,
            predictions: [ExportedTouchCandidatePrediction(from: event(interactionTime: 9.9))]
        )
        let predictions = TouchCandidateCaptureExportAdapter.predictionDataset(from: export)
        let mapping = TouchCandidateCaptureExportAdapter.timestampMapping(from: export)!
        let groundTruth = GroundTruthDataset(assessmentId: "A1", timebase: .videoSeconds, touches: [GroundTruthTouch(id: "g1", timeSeconds: 10.0)])
        let result = try TouchCandidateEvaluator.evaluate(groundTruth: groundTruth, predictions: predictions, mapping: mapping)
        XCTAssertEqual(result.matches.first?.predictedTimeSeconds ?? -1, 10.0, accuracy: 1e-9)
    }
}
