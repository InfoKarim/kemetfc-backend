//
//  RuntimeEnvironment.swift
//  KemetFCTracker
//
//  Reliable, non-DEBUG-dependent runtime detection of "this process is
//  running as an XCTest host" (2026-10-01, Phase 2.5 safety fix). A real
//  production incident required this: running XCTest on a physical
//  device launches the REAL app binary (this project has no separate
//  minimal test-host target), so the real AssessmentSessionViewModel
//  .start()/scenePhase/NWPathMonitor machinery was firing automatic
//  pending-upload retries during ordinary unit-test runs — once
//  incrementing a real, already-failing match recording's uploadAttempts
//  from 16 to 19 with no deliberate action taken.
//
//  `XCTestConfigurationFilePath` is set by Xcode/xctest ONLY on a process
//  actually launched to host tests — true on both Debug and Release
//  configurations, and true in CI — unlike a `#if DEBUG` check, which
//  would (a) not even compile out in Release and (b) conflates "a Debug
//  build" with "a test run," two genuinely different conditions.
//

import Foundation

public enum RuntimeEnvironment {
    /// True only when THIS process was launched by Xcode/xctest to host
    /// XCTest — never true for an ordinary user-launched app, in any
    /// build configuration.
    public static var isRunningAsXCTestHost: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }
}
