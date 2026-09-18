//
//  QRScanDebouncer.swift
//  KemetFCTracker
//
//  Pure Foundation, no AVFoundation dependency — same seam as
//  TrackTypes.swift, so this is testable without a camera.
//
//  AVCaptureMetadataOutput reports the SAME physical QR code on every
//  frame it remains in view (often 30-60 times/second) — without this,
//  a single badge held in front of the camera would fire the
//  resolve-token network call and push the confirmation screen dozens
//  of times per second (spec: "Handle ... duplicate QR tokens
//  gracefully"). This is a UI/network debounce, not a security control —
//  the backend's own token revocation (PlayerCheckInService.mint_token)
//  is what actually prevents a stale token from resolving.
//

import Foundation

public struct QRScanDebouncer {
    private var lastScannedValue: String?
    private var lastScannedAt: Date?
    private let cooldown: TimeInterval

    public init(cooldown: TimeInterval = 3.0) {
        self.cooldown = cooldown
    }

    /// True exactly when `value` should actually be acted on: either
    /// it's different from the last scan, or the cooldown for a repeat
    /// of the SAME value has elapsed — so deliberately re-presenting the
    /// same badge (e.g. after dismissing an error) still works.
    public mutating func shouldProcess(_ value: String, at now: Date = Date()) -> Bool {
        defer {
            lastScannedValue = value
            lastScannedAt = now
        }
        guard value == lastScannedValue, let lastScannedAt else { return true }
        return now.timeIntervalSince(lastScannedAt) >= cooldown
    }

    public mutating func reset() {
        lastScannedValue = nil
        lastScannedAt = nil
    }
}

/// Client-side shape check before ever calling the backend — a coach
/// pointing the scanner at some unrelated QR code (a URL, a WiFi
/// password, ...) should get an immediate "not a KEMET code" response,
/// not a round trip to the server. This is NOT the security boundary
/// (PlayerCheckInService.resolve_token on the backend is, since only it
/// can verify the token's hash actually maps to a live, unrevoked,
/// unexpired row) — purely a fast client-side filter matching the
/// prefix PlayerCheckInService.mint_token generates
/// (app/services/player_checkin_service.py: TOKEN_PREFIX).
public enum KemetCheckInToken {
    public static let prefix = "KEMETCHK-"

    public static func isPlausibleToken(_ value: String) -> Bool {
        value.hasPrefix(prefix) && value.count > prefix.count
    }
}
