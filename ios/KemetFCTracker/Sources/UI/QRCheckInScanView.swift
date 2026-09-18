//
//  QRCheckInScanView.swift
//  KemetFCTracker
//
//  The QR Scan tab of PlayerSelectionView (spec section 5): a live
//  camera preview decoding AVFoundation QR metadata objects, reusing the
//  SAME AVCaptureSession CameraCaptureManager already owns (never a
//  second, conflicting session), added only while this view is on
//  screen and removed the moment it isn't (see CameraCaptureManager
//  .startQRScanning/.stopQRScanning).
//
//  This view owns ONLY the scan-and-resolve step. Confirmation
//  (name/photo/age group/team, Confirm Player / Choose Different
//  Player) is PlayerSelectionView's job, via `onResolved` — this view's
//  only output is "a token was scanned and resolved to a player, or
//  resolution failed."
//

import AVFoundation
import SwiftUI

/// What a scanned check-in token resolved to — success carries the
/// player; failure carries the coach-facing message the backend already
/// crafted per failure reason (invalid/expired/revoked — see
/// PlayerCheckInService on the backend and AssessmentSessionViewModel
/// .resolveCheckInToken on this end).
public enum CheckInResolution {
    case success(KemetPlayerSummary)
    case failure(message: String)
}

public struct QRCheckInScanView: View {
    @ObservedObject var capture: CameraCaptureManager
    let resolveCheckInToken: (String) async -> CheckInResolution

    let onResolved: (CheckInResolution) -> Void

    @State private var isResolving = false
    @State private var scanningStartFailed = false

    public init(
        capture: CameraCaptureManager,
        resolveCheckInToken: @escaping (String) async -> CheckInResolution,
        onResolved: @escaping (CheckInResolution) -> Void
    ) {
        self.capture = capture
        self.resolveCheckInToken = resolveCheckInToken
        self.onResolved = onResolved
    }

    public var body: some View {
        ZStack {
            CameraPreviewRepresentable(session: capture.session)
                .aspectRatio(3.0 / 4.0, contentMode: .fit)
                .overlay(scanFrameOverlay)
                .clipShape(RoundedRectangle(cornerRadius: 16))

            if isResolving {
                Color.black.opacity(0.4)
                ProgressView("Checking player...")
                    .tint(.white)
                    .foregroundStyle(.white)
                    .padding()
                    .background(.thinMaterial)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
        .frame(maxWidth: .infinity)
        .overlay(alignment: .bottom) {
            if scanningStartFailed {
                Text("Could not start the QR scanner — camera may still be initializing.")
                    .font(.footnote)
                    .foregroundStyle(.white)
                    .padding(8)
                    .frame(maxWidth: .infinity)
                    .background(.red.opacity(0.85))
            } else {
                Text("Point the camera at the player's KEMET check-in QR code.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(8)
                    .frame(maxWidth: .infinity)
                    .background(.thinMaterial)
            }
        }
        .onAppear(perform: startScanning)
        .onDisappear(perform: capture.stopQRScanning)
    }

    private var scanFrameOverlay: some View {
        RoundedRectangle(cornerRadius: 12)
            .stroke(Color.white.opacity(0.85), lineWidth: 3)
            .padding(40)
    }

    private func startScanning() {
        let coordinator = ScanCoordinator(
            onCodeScanned: { rawValue in
                Task { await handleScannedValue(rawValue) }
            }
        )
        // Retained by the capture output's delegate reference itself
        // (setMetadataObjectsDelegate keeps a weak reference on some
        // OS versions, so this view's own strong `activeCoordinator`
        // is what actually keeps it alive for the scan's lifetime).
        activeCoordinator = coordinator
        let started = capture.startQRScanning(delegate: coordinator, queue: .main)
        scanningStartFailed = !started
    }

    @State private var activeCoordinator: ScanCoordinator?

    @MainActor
    private func handleScannedValue(_ rawValue: String) async {
        guard !isResolving else { return }
        guard KemetCheckInToken.isPlausibleToken(rawValue) else {
            onResolved(.failure(message: "That QR code isn't a KEMET player check-in code."))
            return
        }

        isResolving = true
        let resolution = await resolveCheckInToken(rawValue)
        isResolving = false
        onResolved(resolution)
    }

    /// AVCaptureMetadataOutputObjectsDelegate callback arrives on the
    /// queue passed to startQRScanning (.main here) — deliberately kept
    /// off the high-frequency video-data-output queue used for
    /// inference, since QR decoding is infrequent and cheap, and this
    /// way no actor hop is needed before touching SwiftUI state.
    private final class ScanCoordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        private let onCodeScanned: (String) -> Void
        private var debouncer = QRScanDebouncer()

        init(onCodeScanned: @escaping (String) -> Void) {
            self.onCodeScanned = onCodeScanned
        }

        func metadataOutput(
            _ output: AVCaptureMetadataOutput,
            didOutput metadataObjects: [AVMetadataObject],
            from connection: AVCaptureConnection
        ) {
            guard let qrObject = metadataObjects
                .compactMap({ $0 as? AVMetadataMachineReadableCodeObject })
                .first(where: { $0.type == .qr }),
                let value = qrObject.stringValue,
                debouncer.shouldProcess(value)
            else { return }
            onCodeScanned(value)
        }
    }
}
