//
//  TouchCandidateDatasetManifestTests.swift
//  KemetFCTrackerTests
//
//  Covers Part 19's DATASET MANIFEST items (1-8).
//

import XCTest
@testable import KemetFCTracker

final class TouchCandidateDatasetManifestTests: XCTestCase {
    private func entry(
        _ id: String, split: DatasetSplit = .development, sync: SynchronizationProvenance = .unavailable,
        inclusion: AssessmentInclusionStatus = .included, groupId: String? = nil
    ) -> TouchCandidateDatasetAssessmentEntry {
        TouchCandidateDatasetAssessmentEntry(assessmentId: id, synchronizationStatus: sync, split: split, inclusionStatus: inclusion, groupId: groupId)
    }

    // 1. valid manifest
    func testValidManifestConstructsSuccessfully() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1"), entry("A2")])
        XCTAssertEqual(manifest.assessments.count, 2)
    }

    // 2. duplicate assessment IDs rejected
    func testDuplicateAssessmentIdsRejected() {
        XCTAssertThrowsError(try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1"), entry("A1")])) { error in
            XCTAssertEqual(error as? TouchCandidateDatasetError, .duplicateAssessmentId("A1"))
        }
    }

    // 3. unknown split rejected (naturally, via strict Codable enum decoding)
    func testUnknownSplitRejectedOnDecode() throws {
        let json = """
        {"schemaVersion":1,"datasetId":"D1","createdAt":0,"assessments":[
          {"assessmentId":"A1","synchronizationStatus":"unavailable","split":"not_a_real_split","inclusionStatus":{"included":{}}}
        ]}
        """.data(using: .utf8)!
        XCTAssertThrowsError(try TouchCandidateValidationIO.decoder().decode(TouchCandidateDatasetManifest.self, from: json))
    }

    // 4. explicit exclusion preserved
    func testExplicitExclusionPreserved() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", inclusion: .excluded(reason: .manualExclusion))])
        XCTAssertEqual(manifest.assessments.first?.inclusionStatus, .excluded(reason: .manualExclusion))
    }

    // 5. missing artifact represented honestly (nil references, not empty-string placeholders)
    func testMissingArtifactReferencesAreNilNotFabricated() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1")])
        let a = manifest.assessments.first
        XCTAssertNil(a?.captureExportReference)
        XCTAssertNil(a?.groundTruthReference)
        XCTAssertNil(a?.videoReference)
    }

    // 6. groupId preserved
    func testGroupIdPreserved() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [entry("A1", groupId: "player-session-7")])
        XCTAssertEqual(manifest.assessments.first?.groupId, "player-session-7")
    }

    // 7. deterministic JSON round-trip
    func testDeterministicJSONRoundTrip() throws {
        let manifest = try TouchCandidateDatasetManifest(
            datasetId: "D1", createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            assessments: [entry("A1", split: .holdout, sync: .approximateHistoricalOffset, groupId: "g1")]
        )
        let data1 = try TouchCandidateValidationIO.encoder().encode(manifest)
        let data2 = try TouchCandidateValidationIO.encoder().encode(manifest)
        XCTAssertEqual(data1, data2)
        let decoded = try TouchCandidateValidationIO.decoder().decode(TouchCandidateDatasetManifest.self, from: data1)
        XCTAssertEqual(decoded, manifest)
    }

    // 8. schema version preserved
    func testSchemaVersionPreserved() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [])
        XCTAssertEqual(manifest.schemaVersion, TouchCandidateDatasetManifest.currentSchemaVersion)
        let data = try TouchCandidateValidationIO.encoder().encode(manifest)
        let decoded = try TouchCandidateValidationIO.decoder().decode(TouchCandidateDatasetManifest.self, from: data)
        XCTAssertEqual(decoded.schemaVersion, 1)
    }

    // Cross-split group leakage detection (Part 5)
    func testCrossSplitGroupLeakageDetected() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, groupId: "shared"),
            entry("A2", split: .holdout, groupId: "shared"),
            entry("A3", split: .development, groupId: "isolated"),
        ])
        XCTAssertEqual(manifest.crossSplitGroupLeakage(), ["shared"])
    }

    func testNoLeakageWhenGroupsStayWithinOneSplit() throws {
        let manifest = try TouchCandidateDatasetManifest(datasetId: "D1", assessments: [
            entry("A1", split: .development, groupId: "g1"),
            entry("A2", split: .development, groupId: "g1"),
        ])
        XCTAssertTrue(manifest.crossSplitGroupLeakage().isEmpty)
    }
}
