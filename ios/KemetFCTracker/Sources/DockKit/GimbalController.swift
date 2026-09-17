//
//  GimbalController.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Converts TrackingCoordinator's chosen framing target into DockKit
//  custom-tracking observations and feeds them to the accessory at a
//  rate-limited interval. APIs used, verified live against Apple's
//  documentation this session:
//
//    DockAccessory.Observation(identifier:type:rect:faceYawAngle:)  (17.0)
//    DockAccessory.CameraInformation(captureDevice:cameraPosition:
//                                     orientation:cameraIntrinsics:
//                                     referenceDimensions:)          (17.0)
//    DockAccessory.track(_:cameraInformation:) -> async throws       (17.0)
//        (the [Observation] overload — NOT the [AVMetadataObject]
//        overload, since our detections come from Vision, not
//        AVCaptureMetadataOutput)
//    DockAccessory.setRegionOfInterest(_:) / .regionOfInterest       (17.0)
//    DockAccessory.setFramingMode(_:) — .automatic/.center/.left/
//                                        .right                       (17.0)
//    DockAccessory.setLimits(_:) with DockAccessory.Limits(yaw:
//                                        pitch:roll:)                 (17.0)
//    DockAccessory.setAngularVelocity(_:) — Vector3D (Spatial)        (17.0)
//    DockAccessory.setOrientation(_:duration:relative:) -> Progress   (17.0)
//
//  Apple's own sample explicitly documents calling track(_:cameraInformation:)
//  "at an interval between 10 and 30 times per second" — this file
//  enforces that as trackingIntervalSeconds, and NEVER calls it from the
//  raw camera-frame callback directly (see spec section 10: "Do not
//  flood DockKit with uncontrolled frame-rate calls").
//

import AVFoundation
import DockKit
import Combine
import Foundation
import Spatial
import os.log

public enum DockKitTestError: Error, LocalizedError {
    case noAccessoryAttached
    public var errorDescription: String? {
        switch self {
        case .noAccessoryAttached: return "No dock accessory is attached"
        }
    }
}

@MainActor
public final class GimbalController: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "GimbalController")

    /// Apple's own custom-tracking sample: "Call track(_:cameraInformation:)
    /// at an interval between 10 and 30 times per second." We default to
    /// the middle of that range — fast enough for SPORT-profile soccer
    /// movement, without saturating the accessory's Bluetooth/Wi-Fi link.
    public var trackingIntervalSeconds: TimeInterval = 1.0 / 20.0
    public var speedProfile: TrackingSpeedProfile = .sport {
        didSet { applySpeedProfileLimits() }
    }

    /// Failsafe (spec section: gimbal failure during an active
    /// assessment must NEVER stop recording, and must NEVER silently
    /// resume on its own): set once `track(_:cameraInformation:)` fails
    /// this many times in a row, at which point updateTarget(...) stops
    /// attempting further sends until resumeControlAfterFailsafe() is
    /// called explicitly (a coach tap), even if the accessory reconnects
    /// on its own in the meantime.
    public var maxConsecutiveTrackFailuresBeforeFailsafe = 3
    @Published public private(set) var isControlLost = false
    private var consecutiveTrackFailures = 0

    private var accessory: DockAccessory?
    private var lastSendTime: CFTimeInterval = 0
    private var isSending = false

    public init() {}

    public func attach(accessory: DockAccessory) {
        self.accessory = accessory
        applySpeedProfileLimits()
        Task {
            do {
                // KEMET always supplies its own observations — Apple's
                // built-in system tracking must stay OFF so it never
                // reframes onto an unselected person (see DockKitManager
                // header comment).
                try await DockAccessoryManager.shared.setSystemTrackingEnabled(false)
                try await accessory.setFramingMode(.automatic)
            } catch {
                log.error("Initial gimbal configuration failed: \(error.localizedDescription)")
            }
        }
    }

    public func detach() {
        accessory = nil
    }

    /// SLOW / NORMAL / SPORT / CUSTOM soft rotation limits — DockKit's
    /// Limit type separates *how far* an axis may rotate (positionRange)
    /// from *how fast* (maximumSpeed, radians/second); this method holds
    /// positionRange fixed (the physical range sensible for a soccer
    /// assessment) and varies only maximumSpeed by profile — the
    /// anti-"blindly maximize motor speed" guardrail from spec section 12.
    private func applySpeedProfileLimits() {
        guard let accessory else { return }
        let maxSpeedRadiansPerSecond: Double
        switch speedProfile {
        case .slow: maxSpeedRadiansPerSecond = .pi / 6      // ~30 deg/s
        case .normal: maxSpeedRadiansPerSecond = .pi / 3    // ~60 deg/s
        case .sport: maxSpeedRadiansPerSecond = .pi * 0.75  // ~135 deg/s — fast lateral running
        case .custom: return                                // caller sets limits explicitly
        }
        do {
            let yaw = try DockAccessory.Limits.Limit(
                positionRange: -.pi..<Double.pi,
                maximumSpeed: maxSpeedRadiansPerSecond
            )
            // Bounded tilt (never point the camera at sky/ground during
            // a ground-level soccer assessment) and minimal roll — a
            // sports gimbal has no reason to bank the horizon.
            let pitch = try DockAccessory.Limits.Limit(
                positionRange: -(.pi / 4)..<(.pi / 4),
                maximumSpeed: maxSpeedRadiansPerSecond
            )
            let roll = try DockAccessory.Limits.Limit(
                positionRange: -(.pi / 12)..<(.pi / 12),
                maximumSpeed: maxSpeedRadiansPerSecond
            )
            try accessory.setLimits(DockAccessory.Limits(yaw: yaw, pitch: pitch, roll: roll))
        } catch {
            log.error("setLimits failed: \(error.localizedDescription)")
        }
    }

    /// The single entry point TrackingCoordinator calls every processed
    /// frame — internally rate-limited to trackingIntervalSeconds, so
    /// callers don't need to think about DockKit's own pacing
    /// requirements. Silently drops (does not queue) calls that arrive
    /// faster than the interval, since a stale target vector is worse
    /// than skipping a frame — DockKit will get an up-to-date one on
    /// the very next tick.
    public func updateTarget(
        framingTarget: NormalizedRect,
        captureDevice: AVCaptureDevice,
        cameraIntrinsics: matrix_float3x3?,
        referenceDimensions: CGSize
    ) {
        // Failsafe engaged: recording continues regardless (this
        // controller has no say over CameraCaptureManager), but gimbal
        // motion attempts stop until a coach explicitly acknowledges and
        // resumes — never silently, even if the accessory reconnects.
        guard !isControlLost else { return }
        guard let accessory else { return }

        let now = CACurrentMediaTime()
        guard now - lastSendTime >= trackingIntervalSeconds, !isSending else { return }
        lastSendTime = now
        isSending = true

        let cameraInfo = DockAccessory.CameraInformation(
            captureDevice: captureDevice.deviceType,
            cameraPosition: captureDevice.position,
            orientation: .landscapeRight,
            cameraIntrinsics: cameraIntrinsics,
            referenceDimensions: referenceDimensions
        )

        let observation = DockAccessory.Observation(
            identifier: 0,
            type: .humanBody,
            rect: CGRect(
                x: framingTarget.x, y: framingTarget.y,
                width: framingTarget.width, height: framingTarget.height
            ),
            faceYawAngle: nil
        )

        Task {
            defer { self.isSending = false }
            do {
                try await accessory.track([observation], cameraInformation: cameraInfo)
                self.consecutiveTrackFailures = 0
            } catch {
                self.log.error("track(_:cameraInformation:) failed: \(error.localizedDescription)")
                self.consecutiveTrackFailures += 1
                if self.consecutiveTrackFailures >= self.maxConsecutiveTrackFailuresBeforeFailsafe {
                    self.isControlLost = true
                    self.log.error("Gimbal failsafe engaged after \(self.consecutiveTrackFailures) consecutive track() failures — recording continues; gimbal motion stopped until explicit resume.")
                }
            }
        }
    }

    /// Explicit-only resume (spec: never automatic) — a coach
    /// acknowledging the "GIMBAL CONTROL LOST" banner and choosing to
    /// try again, typically after confirming the accessory is docked
    /// again. Re-applies speed-profile limits since a fresh `attach(
    /// accessory:)` may have occurred while control was lost.
    public func resumeControlAfterFailsafe() {
        consecutiveTrackFailures = 0
        isControlLost = false
        applySpeedProfileLimits()
    }

    /// "Center Gimbal" manual override (spec section 25) — an absolute
    /// orientation reset, not a relative nudge, so it always means
    /// exactly what the button says regardless of current position.
    public func centerGimbal() {
        guard let accessory else { return }
        do {
            _ = try accessory.setOrientation(
                Vector3D(x: 0, y: 0, z: 0),
                duration: .seconds(1),
                relative: false
            )
        } catch {
            log.error("centerGimbal failed: \(error.localizedDescription)")
        }
    }

    /// DockKit Test Mode's manual single-shot send — deliberately NOT
    /// rate-limited or failsafe-gated like updateTarget(...) (a coach
    /// running Test Mode wants each tap's exact result, including its
    /// exact error, not a silently-dropped call) and throws instead of
    /// only logging, so the Test Mode UI can display the real error
    /// text (spec: "inspect errors").
    public func sendTestObservation(rect: NormalizedRect, captureDevice: AVCaptureDevice) async throws {
        guard let accessory else {
            throw DockKitTestError.noAccessoryAttached
        }
        let cameraInfo = DockAccessory.CameraInformation(
            captureDevice: captureDevice.deviceType,
            cameraPosition: captureDevice.position,
            orientation: .landscapeRight,
            cameraIntrinsics: nil,
            referenceDimensions: CGSize(width: 1920, height: 1080)
        )
        let observation = DockAccessory.Observation(
            identifier: 0,
            type: .humanBody,
            rect: CGRect(x: rect.x, y: rect.y, width: rect.width, height: rect.height),
            faceYawAngle: nil
        )
        try await accessory.track([observation], cameraInformation: cameraInfo)
    }

    /// Pauses active gimbal motor correction WITHOUT tearing down the
    /// dock connection — used both for the manual "Pause Tracking"
    /// control and automatically when SubjectReacquisition decides
    /// confidence is too low to keep moving the camera (spec section 13:
    /// "If confidence becomes too low: Stop aggressive gimbal movement").
    public func pauseMotorCorrection() {
        guard let accessory else { return }
        do {
            try accessory.setAngularVelocity(Vector3D(x: 0, y: 0, z: 0))
        } catch {
            log.error("pauseMotorCorrection failed: \(error.localizedDescription)")
        }
    }
}
