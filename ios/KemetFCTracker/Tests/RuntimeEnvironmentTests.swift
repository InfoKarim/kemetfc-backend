//
//  RuntimeEnvironmentTests.swift
//  KemetFCTrackerTests
//
//  Covers Phase 2.5 Part B/H: the XCTest-host detection mechanism that
//  AssessmentSessionViewModel.retryAllPending() guards on.
//

import XCTest
@testable import KemetFCTracker

final class RuntimeEnvironmentTests: XCTestCase {
    /// Every test in this entire target runs as an XCTest host by
    /// definition, so this must always be true here — the one fact the
    /// Phase 2.5 safety guard depends on.
    func testIsRunningAsXCTestHostIsTrueDuringATestRun() {
        XCTAssertTrue(RuntimeEnvironment.isRunningAsXCTestHost)
    }

    /// The detection reads `XCTestConfigurationFilePath`, which Xcode/xctest
    /// sets on the process environment — not a `#if DEBUG` compile-time
    /// flag — so it is true in Release test runs and CI exactly the same
    /// way, and is never a build-configuration artifact.
    func testDetectionIsDrivenByTheXCTestEnvironmentVariableNotADebugFlag() {
        XCTAssertNotNil(
            ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"],
            "this test's own process must have the variable the guard reads"
        )
        XCTAssertTrue(RuntimeEnvironment.isRunningAsXCTestHost)
    }
}
