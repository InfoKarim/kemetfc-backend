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

        // Only react to a "tracking stand" category accessory — the
        // Flow 2 Pro identifies itself this way. A different accessory
        // category (if the user later plugs in something else DockKit
        // supports) is deliberately ignored rather than silently
        // treated as our gimbal.
        guard accessory.identifier.category == .trackingStand else { return }

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
