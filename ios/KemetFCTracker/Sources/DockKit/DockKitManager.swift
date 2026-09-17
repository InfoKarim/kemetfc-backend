//
//  DockKitManager.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  Wraps DockAccessoryManager (the DockKit singleton) to discover and
//  connect to the Insta360 Flow 2 Pro as a DockKit-compatible accessory,
//  and to surface a UI-friendly GimbalConnectionState. Every API used
//  here was confirmed live against developer.apple.com/documentation
//  during this session:
//
//    DockAccessoryManager.shared                                (iOS 17.0)
//    DockAccessoryManager.shared.accessoryStateChanges           (iOS 17.0)
//    DockAccessoryManager.shared.isSystemTrackingEnabled          (iOS 17.0)
//    DockAccessoryManager.shared.setSystemTrackingEnabled(_:)     (iOS 17.0)
//    DockAccessory.State: .docked / .undocked                     (iOS 17.0)
//    DockAccessory.StateChange: { accessory, state,
//                                  trackingButtonEnabled }
//    DockAccessory.Category.trackingStand                         (iOS 17.0)
//    DockAccessory.accessoryEvents  (button/shutter/zoom events)   (iOS 17.4)
//    DockAccessory.batteryStates                                  (iOS 18.0)
//
//  Apple's own DockKit sample ("Track custom objects in a frame") is the
//  reference this whole module (this file + GimbalController.swift) was
//  built against.
//
//  KEMET NEVER calls setSystemTrackingEnabled(true) — Apple's built-in
//  person/saliency tracking has no concept of "the coach-selected
//  player," and would happily reframe onto ANY person in view. System
//  tracking is left OFF; KemetFCTracker supplies 100% custom
//  observations via GimbalController.track(...), driven by
//  TrackingCoordinator's own player-lock logic (see spec section 10,
//  "DOCKKIT CUSTOM TRACKING").
//

import DockKit
import Combine
import Foundation
import os.log

@MainActor
public final class DockKitManager: ObservableObject {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "DockKitManager")

    @Published public private(set) var connectionState: GimbalConnectionState = .disconnected
    @Published public private(set) var connectedAccessory: DockAccessory?
    @Published public private(set) var lastError: String?
    @Published public private(set) var batteryLevel: Double?
    @Published public private(set) var firmwareVersion: String?
    /// From DockAccessory.StateChange.trackingButtonEnabled — surfaced
    /// for the Gimbal Diagnostics screen (spec: "accessory detected/
    /// category/name/battery/firmware/tracking capability/current
    /// state"). Whether the accessory's own physical tracking button is
    /// currently usable; not the same thing as KEMET's own custom
    /// tracking, which never uses Apple's system tracking at all (see
    /// this file's header).
    @Published public private(set) var trackingButtonEnabled: Bool?
    @Published public private(set) var lastAccessoryName: String?
    @Published public private(set) var lastAccessoryCategory: String?

    private var stateChangeTask: Task<Void, Never>?
    private var batteryTask: Task<Void, Never>?
    private var reconnectAttempt = 0

    public init() {}

    /// Begin observing dock connect/disconnect events. Call once, early
    /// in the app's lifecycle (see AssessmentSessionViewModel) — this
    /// does NOT itself connect to anything; it just watches for a
    /// Flow 2 Pro that iOS has already physically detected being docked.
    public func startMonitoring() {
        stateChangeTask?.cancel()
        stateChangeTask = Task { [weak self] in
            guard let self else { return }
            do {
                let changes = try DockAccessoryManager.shared.accessoryStateChanges
                for try await change in changes {
                    await self.handle(stateChange: change)
                }
            } catch {
                await MainActor.run {
                    self.lastError = "DockKit unavailable: \(error.localizedDescription)"
                    self.connectionState = .disconnected
                }
                self.log.error("accessoryStateChanges stream failed: \(error.localizedDescription)")
            }
        }
    }

    public func stopMonitoring() {
        stateChangeTask?.cancel()
        stateChangeTask = nil
        batteryTask?.cancel()
        batteryTask = nil
    }

    private func handle(stateChange: DockAccessory.StateChange) async {
        let accessory = stateChange.accessory

        // RESOLVED (Phase 2 audit) — this was flagged as an unverified
        // assumption in the Phase 1 report. Re-checked against Apple's
        // live docs: DockAccessory.Category has EXACTLY ONE documented
        // case, .trackingStand — there is no other category to
        // possibly confuse this with. Every DockKit-compatible
        // accessory, Flow 2 Pro included, necessarily reports this
        // category, since the enum defines nothing else. Insta360's own
        // Flow 2 Pro DockKit documentation (onlinemanual.insta360.com,
        // /flow2pro/en-us/operating-tutorials/track/apple-dockkit)
        // confirms iOS 17.0+ (17.4+ recommended) and NFC-based pairing,
        // but does not itself document a category value — moot, since
        // no alternative exists in the API for it to be. This guard is
        // effectively "is this a DockKit accessory at all," not a
        // Flow-2-Pro-specific guess.
        guard accessory.identifier.category == .trackingStand else { return }

        trackingButtonEnabled = stateChange.trackingButtonEnabled
        lastAccessoryName = accessory.identifier.name
        lastAccessoryCategory = String(describing: accessory.identifier.category)

        switch stateChange.state {
        case .docked:
            connectedAccessory = accessory
            connectionState = .connected
            lastError = nil
            reconnectAttempt = 0
            firmwareVersion = accessory.firmwareVersion
            observeBattery(for: accessory)
            log.info("Dock accessory docked: \(accessory.identifier.name, privacy: .public)")

        case .undocked:
            connectedAccessory = nil
            batteryLevel = nil
            batteryTask?.cancel()
            if connectionState == .trackingActive || connectionState == .connected {
                connectionState = .reconnecting
                attemptReconnect()
            } else {
                connectionState = .disconnected
            }
            log.info("Dock accessory undocked")

        @unknown default:
            log.error("Unknown DockAccessory.State case — treating as disconnected")
            connectionState = .disconnected
        }
    }

    /// A brief grace period before declaring the gimbal truly gone — a
    /// physical gimbal can report a momentary undock during vibration/
    /// bump without the coach actually having removed the phone.
    private func attemptReconnect() {
        reconnectAttempt += 1
        guard reconnectAttempt <= 3 else {
            connectionState = .disconnected
            lastError = "Gimbal not found after 3 reconnect attempts"
            return
        }
        Task {
            try? await Task.sleep(for: .seconds(2))
            if connectedAccessory == nil && connectionState == .reconnecting {
                connectionState = .disconnected
            }
        }
    }

    private func observeBattery(for accessory: DockAccessory) {
        batteryTask?.cancel()
        batteryTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await states in try accessory.batteryStates {
                    guard let first = states.first else { continue }
                    await MainActor.run {
                        self.batteryLevel = Double(first.batteryLevel)
                    }
                }
            } catch {
                self.log.error("batteryStates stream failed: \(error.localizedDescription)")
            }
        }
    }

    /// UI-facing "TRACKING ACTIVE" / "TRACKING LOST" — driven by
    /// GimbalController once it starts feeding observations, NOT by
    /// this manager itself (DockKitManager only knows about physical
    /// dock/undock, not our own custom tracking's health).
    public func setTrackingActive(_ active: Bool) {
        guard connectedAccessory != nil else { return }
        connectionState = active ? .trackingActive : .trackingLost
    }

    public var isConnected: Bool {
        connectedAccessory != nil
    }
}
