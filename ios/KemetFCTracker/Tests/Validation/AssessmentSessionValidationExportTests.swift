//
//  AssessmentSessionValidationExportTests.swift
//  KemetFCTrackerTests
//
//  Proves the ONE genuinely new, independently-testable piece of the
//  real-data capture bridge integration: deterministic Application Support
//  path construction (Part 15 item 10). `AssessmentSessionViewModel` has no
//  other unit tests anywhere in this codebase — it requires real
//  AVFoundation/network dependencies, so full `stopAssessment()` integration
//  behavior (items 1-3, 12) is verified by CODE INSPECTION instead, not a
//  runtime test, and documented as such in the phase report:
//    - item 1/2/3: `exportTouchCandidateValidationArtifact(record:)` is
//      called from EXACTLY ONE place — inside `stopAssessment()`'s
//      `.valid(let warning)` case, after recording integrity has already
//      been confirmed. The `.invalid(let reason)` case returns from the
//      switch without ever reaching that call. Export failures inside are
//      caught and logged, never rethrown, never written back onto `record`.
//    - item 12: there is exactly one call site (grep-verified), not inside
//      any per-frame/per-event loop.
//  Items 4-9/11 (capturePtsOriginSeconds preserved, videoOriginSeconds/
//  offsetSeconds nil, provenance never claims measured, empty predictions
//  valid, correct identity, no cross-assessment contamination) are already
//  proven at the pure-function level by `TouchCandidateCaptureExportTests`,
//  since `exportTouchCandidateValidationArtifact` is a thin, direct wrapper
//  around `TouchCandidateCaptureExport.build(...)` with no new logic beyond
//  wiring + file I/O.
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class AssessmentSessionValidationExportTests: XCTestCase {
    private func makeViewModel() -> AssessmentSessionViewModel {
        let client = KemetAPIClient(configuration: KemetAPIConfiguration(baseURL: URL(string: "https://example.invalid")!))
        return AssessmentSessionViewModel(apiClient: client)
    }

    func testValidationDirectoryPathIsDeterministicForTheSameAssessmentId() throws {
        let viewModel = makeViewModel()
        let first = try viewModel.touchCandidateValidationDirectory(assessmentId: "ASSESS-1")
        let second = try viewModel.touchCandidateValidationDirectory(assessmentId: "ASSESS-1")
        XCTAssertEqual(first, second)
    }

    func testValidationDirectoryPathDiffersForDifferentAssessmentIds() throws {
        let viewModel = makeViewModel()
        let a = try viewModel.touchCandidateValidationDirectory(assessmentId: "ASSESS-A")
        let b = try viewModel.touchCandidateValidationDirectory(assessmentId: "ASSESS-B")
        XCTAssertNotEqual(a, b, "no cross-assessment contamination — each assessment gets its own directory")
    }

    func testValidationDirectoryPathContainsExpectedComponents() throws {
        let viewModel = makeViewModel()
        let directory = try viewModel.touchCandidateValidationDirectory(assessmentId: "ASSESS-XYZ")
        XCTAssertTrue(directory.path.contains("KemetFCTracker/Validation/ASSESS-XYZ"), directory.path)
    }

    func testValidationDirectoryIsActuallyCreatedOnDisk() throws {
        let viewModel = makeViewModel()
        let directory = try viewModel.touchCandidateValidationDirectory(assessmentId: "ASSESS-CREATE-\(UUID().uuidString)")
        var isDirectory: ObjCBool = false
        let exists = FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDirectory)
        XCTAssertTrue(exists)
        XCTAssertTrue(isDirectory.boolValue)
        try? FileManager.default.removeItem(at: directory) // test cleanup only
    }
}
