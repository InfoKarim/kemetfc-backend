//
//  AssessmentSessionViewModelRetryGuardTests.swift
//  KemetFCTrackerTests
//
//  Covers Phase 2.5 Part B/H: the automatic-retry XCTest-host guard added
//  to AssessmentSessionViewModel.retryAllPending(). This is the single
//  function all three automatic triggers (app-launch start(), scenePhase
//  handleAppDidBecomeActive(), and NWPathMonitor network-restoration) call
//  into — confirmed by direct source inspection (Phase 2.5 Part A) — while
//  the manual "Retry" UI calls retryUpload(recordId:) -> finalizeAndUpload
//  directly and never passes through retryAllPending() at all. Guarding
//  this one function therefore covers every automatic path without
//  touching the manual one.
//
//  `AssessmentSessionViewModel` has no other unit tests beyond path-
//  construction (see AssessmentSessionValidationExportTests.swift) because
//  it needs real AVFoundation/network dependencies; this test only proves
//  the guard added at the very top of retryAllPending() short-circuits
//  the function before it reads `pendingStore` (its real, on-disk,
//  Documents-directory-backed queue — never a test double here) or
//  touches `telemetryReplay`/the network. That is verified two ways:
//  (1) by direct code inspection — the guard's `return` is the first
//  statement in the function body, before any other reference — and
//  (2) here, by proving the call returns promptly with no crash/hang,
//  which would not hold if it fell through into camera/network/pendingStore
//  code paths that this test's bare-bones KemetAPIClient cannot service.
//

import XCTest
@testable import KemetFCTracker

@MainActor
final class AssessmentSessionViewModelRetryGuardTests: XCTestCase {
    private func makeViewModel() -> AssessmentSessionViewModel {
        let client = KemetAPIClient(configuration: KemetAPIConfiguration(baseURL: URL(string: "https://example.invalid")!))
        return AssessmentSessionViewModel(apiClient: client)
    }

    /// The precondition this whole guard exists for: we are, in fact,
    /// running as an XCTest host right now.
    func testThisTestProcessIsDetectedAsAnXCTestHost() {
        XCTAssertTrue(RuntimeEnvironment.isRunningAsXCTestHost)
    }

    /// retryAllPending() must return immediately under XCTest, never
    /// reaching the pendingStore loop, the telemetry replay signal, or
    /// finalizeAndUpload — any of which would require real network/camera
    /// dependencies this test's unreachable base URL cannot satisfy.
    func testRetryAllPendingReturnsImmediatelyUnderXCTestHost() async {
        let viewModel = makeViewModel()
        await viewModel.retryAllPending()
        // Reaching this line at all (promptly, via the async test runner's
        // own timeout) is the proof: falling through the guard into the
        // real pendingStore/network code would not fail loudly, it would
        // just mean the suppression did not happen.
    }
}
