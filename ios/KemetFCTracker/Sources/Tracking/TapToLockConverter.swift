//
//  TapToLockConverter.swift
//  KemetFCTracker
//
//  STATUS: Compiled AND run with the real Swift 6.3.3 compiler on this
//  machine (`swiftc`, standalone executable — see FramingCalculator.swift's
//  header for why not `swift test`). Pure CoreGraphics/Foundation, no
//  AVFoundation/UIKit/SwiftUI dependency, so this is possible outside Xcode.
//  Result when last run: see Tests/TapToLockConverterTests.swift header.
//
//  Phase 2 fix for a real, previously-open gap: AssessmentTrackingView
//  was hardcoding every tap to frame center regardless of where the
//  coach actually touched the screen (spec: "Do not lock an arbitrary
//  person when the user taps empty space"). This file is the complete,
//  deterministic coordinate pipeline:
//
//    SwiftUI tap point (view space)
//    -> normalized camera-image-space point (undoing aspect-fill)
//    -> candidate detection selection (nearest, only if close enough)
//
//  Orientation note: this assumes the CALLER passes `videoDimensions`
//  already in the same rotated orientation the preview layer renders in
//  (i.e. what the coach visually sees matches what `videoDimensions`
//  describes) — AVCaptureVideoPreviewLayer handles the actual pixel
//  rotation for on-screen display, so once the preview is correctly
//  oriented, only the aspect-fill scale/crop remains to invert here.
//  This assumption is documented, not verified on a real device (no
//  device available) — see VERIFICATION_CHECKLIST.md Stage C.
//

import CoreGraphics
import Foundation

public enum TapToLockConverter {
    /// Inverts AVCaptureVideoPreviewLayer's `.resizeAspectFill` gravity:
    /// the video is scaled up (never down) until it fully covers the
    /// view, uniformly, then centered — cropping whichever dimension
    /// overflows. To map a tap back to camera-space, undo exactly that:
    /// scale by the SAME factor the layer used, then add back the
    /// cropped-off offset.
    public static func normalizedCameraPoint(
        tapPoint: CGPoint,
        viewSize: CGSize,
        videoDimensions: CGSize
    ) -> NormalizedPoint? {
        guard viewSize.width > 0, viewSize.height > 0,
              videoDimensions.width > 0, videoDimensions.height > 0 else { return nil }

        // Aspect FILL uses the LARGER of the two scale factors (the
        // dimension that would otherwise leave a gap is over-scaled
        // until it too covers the view) — aspect FIT would use the
        // smaller; using the wrong one here is the single most likely
        // way to get this silently backwards.
        let scale = max(viewSize.width / videoDimensions.width, viewSize.height / videoDimensions.height)
        let scaledVideoSize = CGSize(width: videoDimensions.width * scale, height: videoDimensions.height * scale)

        // How much of the scaled video overflows the view on each axis
        // — exactly what resizeAspectFill crops away, split evenly
        // since the layer centers the content.
        let cropX = (scaledVideoSize.width - viewSize.width) / 2
        let cropY = (scaledVideoSize.height - viewSize.height) / 2

        let scaledVideoPointX = tapPoint.x + cropX
        let scaledVideoPointY = tapPoint.y + cropY

        let normalizedX = scaledVideoPointX / scaledVideoSize.width
        let normalizedY = scaledVideoPointY / scaledVideoSize.height

        // A tap technically inside the view is always within [0,1] of
        // the FULL scaled video by construction — clamp only to absorb
        // floating-point edge cases at the exact boundary, never to
        // paper over a real out-of-view coordinate.
        return NormalizedPoint(
            x: min(max(normalizedX, 0), 1),
            y: min(max(normalizedY, 0), 1)
        )
    }

    /// Selects the detection the coach meant to tap — inside its
    /// bounding box takes priority; otherwise the nearest detection
    /// CENTER within `maxDistanceIfOutsideBox`. Returns nil for a tap on
    /// empty space far from everyone, which the caller (
    /// TrackingCoordinator.lockPlayer) must treat as "do nothing," never
    /// as "lock whoever's closest regardless of distance."
    public static func selectDetection(
        at point: NormalizedPoint,
        among detections: [PersonDetection],
        maxDistanceIfOutsideBox: Double = 0.08
    ) -> PersonDetection? {
        let containing = detections.filter { detection in
            let box = detection.boundingBox
            return point.x >= box.x && point.x <= box.x + box.width
                && point.y >= box.y && point.y <= box.y + box.height
        }
        if let best = containing.min(by: { $0.boundingBox.center.distance(to: point) < $1.boundingBox.center.distance(to: point) }) {
            return best
        }

        guard let nearest = detections.min(by: { $0.boundingBox.center.distance(to: point) < $1.boundingBox.center.distance(to: point) }) else {
            return nil
        }
        return nearest.boundingBox.center.distance(to: point) <= maxDistanceIfOutsideBox ? nearest : nil
    }
}
