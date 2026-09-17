//
//  ThermalManager.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Real thermal management via ProcessInfo.processInfo.thermalState
//  (the actual OS-reported thermal pressure — NOT a fabricated/guessed
//  proxy like "seconds since assessment started"). Reduces ML inference
//  WORK under pressure; NEVER stops recording on its own (spec: "Never
//  automatically stop recording because of ML thermal pressure unless
//  the OS forces it" — only iOS itself, via its own app-suspension/
//  termination behavior under extreme conditions, has that authority).
//
//  Behavior tiers (documented, not learned):
//    .nominal  — full inference rate.
//    .fair     — inference interval widened 1.5x (fewer ML passes/sec);
//                recording, gimbal tracking, and UI are unaffected.
//    .serious  — inference interval widened 3x; a non-blocking warning
//                is surfaced so the coach understands why tracking looks
//                choppier, without interrupting the assessment.
//    .critical — inference interval widened 6x (ML work reduced close
//                to a floor, not to zero — SOME tracking continues
//                rather than silently going fully dark); recording is
//                still never stopped by this code.
//

import Combine
import Foundation

@MainActor
public final class ThermalManager: ObservableObject {
    @Published public private(set) var thermalState: ProcessInfo.ThermalState = ProcessInfo.processInfo.thermalState

    private var observer: NSObjectProtocol?

    public init() {
        observer = NotificationCenter.default.addObserver(
            forName: ProcessInfo.thermalStateDidChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.thermalState = ProcessInfo.processInfo.thermalState
            }
        }
    }

    deinit {
        if let observer {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    /// TrackingCoordinator multiplies its base inference interval by
    /// this to get the effective one — widening the interval (running
    /// inference less often) is the actual thermal-relief mechanism,
    /// since Vision/CoreML inference is the most GPU/Neural-Engine-
    /// intensive work this app does per frame.
    public var inferenceIntervalMultiplier: Double {
        switch thermalState {
        case .nominal: return 1.0
        case .fair: return 1.5
        case .serious: return 3.0
        case .critical: return 6.0
        @unknown default: return 1.0
        }
    }

    /// A short, coach-facing explanation — never blocks or stops
    /// anything, just informs (spec: recording/gimbal must continue).
    public var userFacingWarning: String? {
        switch thermalState {
        case .nominal, .fair: return nil
        case .serious: return "Device is running warm — tracking updates less often. Recording is unaffected."
        case .critical: return "Device is very warm — tracking significantly reduced to protect the device. Recording is unaffected."
        @unknown default: return nil
        }
    }
}
