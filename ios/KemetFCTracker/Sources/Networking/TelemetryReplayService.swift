//
//  TelemetryReplayService.swift
//  KemetFCTracker
//
//  App-wide recovery of telemetry that was queued but not yet acknowledged
//  (the app was killed, the network was down, a session ended offline).
//  It owns replay so that AssessmentSessionViewModel does not have to: the
//  view model only SIGNALS lifecycle moments (launch, foreground, network
//  restored) by calling replayPending().
//
//  Replay uses the same single-flight uploader path as live tracking, so a
//  session that is still recording is never uploaded twice at once.
//  Pending data is never deleted here — not by age either. Sessions that
//  have sat unacknowledged for 30+ days are only reported in the log.
//

import Foundation
import os.log

public final class TelemetryReplayService: @unchecked Sendable {
    private let log = Logger(subsystem: "com.kemetfc.tracker", category: "TelemetryReplay")
    private let apiClient: KemetAPIClient
    private let store: TelemetryQueueStore
    static let abandonedReportingThreshold: TimeInterval = 30 * 24 * 60 * 60

    public init(apiClient: KemetAPIClient, store: TelemetryQueueStore = .shared) {
        self.apiClient = apiClient
        self.store = store
    }

    public func replayPending() async {
        let sessions = store.sessionsWithPendingTelemetry()
        guard !sessions.isEmpty else { return }
        log.notice("Replaying pending telemetry for \(sessions.count) session(s)")
        for sessionId in sessions {
            await TelemetryUploader(apiClient: apiClient, sessionId: sessionId, store: store).flushPending()
        }
        for sessionId in store.abandonedSessions(olderThan: Self.abandonedReportingThreshold) {
            log.error("Session \(sessionId, privacy: .public) has held unacknowledged telemetry for over 30 days; it is kept, not deleted")
        }
    }
}
