//
//  KemetFCTrackerApp.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  App entry point. Set KEMET_BACKEND_BASE_URL to the deployed backend
//  (https://app.kemetfc.com in production, or the LAN address of a
//  local `uvicorn main:app` for on-field testing against a laptop).
//

import SwiftUI

@main
struct KemetFCTrackerApp: App {
    @StateObject private var session: AssessmentSessionViewModel

    init() {
        let baseURL = URL(string: ProcessInfo.processInfo.environment["KEMET_BACKEND_BASE_URL"] ?? "https://app.kemetfc.com")!
        let apiClient = KemetAPIClient(configuration: .init(baseURL: baseURL))
        _session = StateObject(wrappedValue: AssessmentSessionViewModel(apiClient: apiClient))
    }

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(session)
                .task { await session.start() }
        }
    }
}

/// Minimal root screen wiring PlayerSelectionView -> AssessmentTrackingView
/// per the workflow in spec section 24. A production app would add the
/// gimbal-connection wait state, error alerts for lastError, etc. — kept
/// minimal here since the individual pieces (DockKitManager,
/// AssessmentTrackingView, ...) are where the actual engineering is.
struct RootView: View {
    @EnvironmentObject var session: AssessmentSessionViewModel

    var body: some View {
        if session.confirmedPlayer == nil {
            PlayerSelectionView(
                onPlayerConfirmed: { session.confirmPlayer($0) },
                searchPlayers: { _ in [] } // wire to GET /players?search=... before shipping
            )
        } else if let coordinator = session.trackingCoordinator {
            // The REAL coordinator AssessmentSessionViewModel created in
            // startAssessment(...) and is actively feeding camera frames
            // to — never a fresh throwaway instance the view constructs
            // itself, which would silently desync the HUD from what's
            // actually being tracked.
            AssessmentTrackingView(
                capture: session.cameraCaptureManager,
                dockKit: session.dockKitManager,
                coordinator: coordinator,
                onTapToLock: { point in
                    // Phase 2 fix: this was a literal no-op — tap-to-lock
                    // was completely disconnected end to end, not merely
                    // hardcoded to frame-center. AssessmentTrackingView
                    // has already converted the raw view tap into a
                    // normalized camera-space point; the coordinator
                    // supplies the matching detections/pixel buffer from
                    // that same moment (see TrackingCoordinator
                    // .latestDetections/.latestPixelBuffer) so the tap
                    // handler never needs its own stale copy of either.
                    guard let pixelBuffer = coordinator.latestPixelBuffer else { return }
                    session.handlePlayerTap(point, detections: coordinator.latestDetections, pixelBuffer: pixelBuffer)
                },
                onStartAssessment: {
                    Task { await session.startAssessment(mode: .smartSoccer, speedProfile: .sport) }
                },
                onStopAssessment: {
                    Task { await session.stopAssessment() }
                },
                onConfirmPlayerTracked: { answer in
                    Task { await session.confirmPlayerTracked(answer) }
                }
            )
        } else {
            // Player confirmed, but startAssessment(...) (and therefore
            // trackingCoordinator) hasn't run yet — a real app shows a
            // "Connect Flow 2 Pro / press Start Assessment" screen here;
            // kept minimal since that screen's design is a product
            // decision, not a tracking-engineering one. This is also
            // the natural place to reach Gimbal Diagnostics / DockKit
            // Test Mode — exactly when a coach would want to sanity
            // -check the gimbal BEFORE trusting it during a real
            // assessment (spec: these screens must be reachable, not
            // just written).
            NavigationStack {
                VStack(spacing: 16) {
                    Text("Player confirmed. Connect the Flow 2 Pro, then start the assessment.")
                        .multilineTextAlignment(.center)
                        .padding()
                    Button("Start Assessment") {
                        Task { await session.startAssessment(mode: .smartSoccer, speedProfile: .sport) }
                    }
                    .buttonStyle(.borderedProminent)

                    NavigationLink("Gimbal Diagnostics") {
                        GimbalDiagnosticsView(dockKit: session.dockKitManager)
                    }
                    NavigationLink("DockKit Test Mode") {
                        DockKitTestModeView(dockKit: session.dockKitManager, capture: session.cameraCaptureManager)
                    }
                }
            }
        }
    }
}
