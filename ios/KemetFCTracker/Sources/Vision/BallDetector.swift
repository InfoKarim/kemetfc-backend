//
//  BallDetector.swift
//  KemetFCTracker
//
//  STATUS: Source implementation, unverified — see TrackTypes.swift header.
//
//  HONEST GAP: Apple's Vision framework has NO built-in soccer-ball
//  detector (unlike person detection/body pose, which are real built-in
//  APIs — see PlayerDetector.swift). A production-quality ball detector
//  needs a model trained on soccer footage (small/fast/motion-blurred
//  object detection — e.g. a YOLOv8-nano or similar exported to Core ML
//  via coremltools, or trained directly with Create ML's Object
//  Detection template). KEMET FC has no such trained model or training
//  dataset yet (see ml/DATASET_CARD.md at the repo root — dataset
//  collection has not started), and this session has no ability to
//  train or supply one.
//
//  What this file actually provides:
//   1. `BallDetecting` — the protocol every detector implements, so
//      swapping in a trained model later requires touching only
//      TrackingCoordinator's initializer, never its tracking logic.
//   2. `ClassicalCVBallDetector` — a real, working, non-stub heuristic:
//      converts the incoming frame to raw RGBA8 pixels and delegates to
//      BallBlobDetector (Sources/Vision/BallBlobDetector.swift), a
//      genuinely compiled-and-executed (8/8 synthetic checks passed —
//      see that file's header) connected-component blob search. Not a
//      neural network. Genuinely detects a bright, roughly circular,
//      color-balanced blob against grass — genuinely fails on
//      similarly-colored/shaped objects (white cones, chalk lines,
//      socks) more often than a trained detector would; this is a real,
//      documented limitation, not hidden. Registered in the backend's
//      MLModelRegistryDB as "ball-detector" "classical-cv-v0.1",
//      status="experimental" — never silently presented as equivalent
//      to a trained model, and `ball_model_status` is reported to the
//      backend as "fallback_classical" (never "installed") whenever
//      this detector — rather than a real trained CoreMLBallDetector —
//      is the one actually running, so the UI/telemetry can honestly
//      show "BALL MODEL NOT INSTALLED" instead of implying completeness.
//   3. `CoreMLBallDetector` — the integration point for a REAL trained
//      model. Throws `.modelNotProvided` until you supply a compiled
//      `.mlmodelc` and uncomment the VNCoreMLRequest wiring. See the
//      inline instructions below.
//

import CoreImage
import Foundation
import Vision

public enum BallDetectorError: Error {
    case modelNotProvided
}

public protocol BallDetecting {
    /// A model_name/model_version string matching what's registered in
    /// MLModelRegistryDB — TrackingCoordinator includes this in every
    /// tracking event it logs, so which detector produced a given
    /// session's ball data is always reconstructable later.
    var modelIdentifier: (name: String, version: String) { get }

    /// Matches the backend's `TrackingSessionDB.ball_model_status`
    /// literal exactly ("missing" | "fallback_classical" | "installed").
    /// TrackingCoordinator reports this at session-create time so the
    /// backend/UI can honestly show "BALL MODEL NOT INSTALLED" rather
    /// than implying a trained model exists just because ball tracking
    /// ran (spec: "Do NOT claim ball detection is complete").
    var ballModelStatus: String { get }

    func detectBall(in pixelBuffer: CVPixelBuffer) throws -> BallDetection?

    /// Every plausible ball in the frame, so BallTracker can associate them over
    /// time instead of the detector prematurely choosing one. Detectors that only
    /// ever produce one candidate inherit the default below.
    func detectBallCandidates(in pixelBuffer: CVPixelBuffer) throws -> [BallDetection]
}

extension BallDetecting {
    public func detectBallCandidates(in pixelBuffer: CVPixelBuffer) throws -> [BallDetection] {
        try detectBall(in: pixelBuffer).map { [$0] } ?? []
    }
}

/// Color/circularity heuristic — NOT a trained model. See file header.
public final class ClassicalCVBallDetector: BallDetecting {
    public let modelIdentifier: (name: String, version: String) = ("ball-detector", "classical-cv-v0.1")
    public let ballModelStatus = "fallback_classical"

    private let context = CIContext()

    public init() {}

    public func detectBall(in pixelBuffer: CVPixelBuffer) throws -> BallDetection? {
        try detectBallCandidates(in: pixelBuffer).first
    }

    public func detectBallCandidates(in pixelBuffer: CVPixelBuffer) throws -> [BallDetection] {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let width = ciImage.extent.width
        let height = ciImage.extent.height
        guard width > 0, height > 0 else { return [] }

        // Downscale aggressively before any per-pixel work — this is a
        // cheap heuristic, not a neural network, and must stay cheap to
        // avoid competing with player/pose inference for the same frame
        // budget (spec section 26).
        let scale: CGFloat = 240 / max(width, height)
        let scaledImage = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
        guard let small = context.createCGImage(scaledImage, from: scaledImage.extent),
              let buffer = Self.rgbaPixelBuffer(from: small) else { return [] }

        return BallBlobDetector.findBrightRoughlyCircularBlobs(in: buffer).map { candidate in
            BallDetection(
                boundingBox: NormalizedRect(
                    x: candidate.rect.origin.x / CGFloat(small.width),
                    y: candidate.rect.origin.y / CGFloat(small.height),
                    width: candidate.rect.width / CGFloat(small.width),
                    height: candidate.rect.height / CGFloat(small.height)
                ),
                confidence: Self.confidence(for: candidate)
            )
        }
    }

    /// Honest, deliberately low confidence: a classical heuristic must never claim
    /// the certainty of a trained detector (tracking_quality.py treats it
    /// conservatively). It only ranks candidates by how disc-like they are:
    /// 0.20 (barely passes the gates) up to 0.40 (a clean filled disc).
    static func confidence(for candidate: BallBlobDetector.Candidate) -> Double {
        let discLikeness = min(max((candidate.fillRatio - 0.55) / (0.85 - 0.55), 0), 1) * candidate.aspectRatio
        return 0.20 + 0.20 * discLikeness
    }

    /// Renders a CGImage into a raw RGBA8 buffer so BallBlobDetector (pure
    /// Foundation/CoreGraphics, no CoreImage dependency — see its header
    /// for why that separation lets it be compiled and executed outside
    /// Xcode) can run its connected-component search over real pixels.
    private static func rgbaPixelBuffer(from image: CGImage) -> RGBAPixelBuffer? {
        let width = image.width
        let height = image.height
        guard width > 0, height > 0 else { return nil }

        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let cgContext = CGContext(
            data: &bytes,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }

        cgContext.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        return RGBAPixelBuffer(width: width, height: height, bytes: bytes)
    }
}

/// Integration point for a REAL trained Core ML ball-detection model.
/// To use: run `coach export` (or Create ML) to produce
/// KemetBallDetector.mlmodel, add it to the Xcode project (Xcode
/// auto-compiles it to .mlmodelc), then:
///   1. Uncomment the VNCoreMLModel/VNCoreMLRequest code below.
///   2. Register the resulting model_version in MLModelRegistryDB via
///      POST /tracking/admin/models and flip its status to "active".
///   3. Point TrackingCoordinator at CoreMLBallDetector instead of
///      ClassicalCVBallDetector — no other code changes needed, since
///      both conform to the same BallDetecting protocol.
public final class CoreMLBallDetector: BallDetecting {
    public let modelIdentifier: (name: String, version: String)

    // Deliberately "missing", not "installed": this class is the
    // integration POINT for a real trained model, but detectBall()
    // below still unconditionally throws .modelNotProvided until a real
    // .mlmodelc is compiled in and the reference code is uncommented.
    // Flip this to "installed" only in that same change — never before.
    public let ballModelStatus = "missing"

    public init(modelVersion: String) {
        self.modelIdentifier = ("ball-detector", modelVersion)
    }

    public func detectBall(in pixelBuffer: CVPixelBuffer) throws -> BallDetection? {
        throw BallDetectorError.modelNotProvided

        // Reference implementation once a real .mlmodel exists:
        //
        // let model = try VNCoreMLModel(for: KemetBallDetector().model)
        // let request = VNCoreMLRequest(model: model)
        // let handler = VNImageRequestHandler(cvPixelBuffer: pixelBuffer, options: [:])
        // try handler.perform([request])
        // guard let result = (request.results as? [VNRecognizedObjectObservation])?
        //     .max(by: { $0.confidence < $1.confidence }) else { return nil }
        // return BallDetection(
        //     boundingBox: NormalizedRect(
        //         x: result.boundingBox.origin.x,
        //         y: 1 - result.boundingBox.origin.y - result.boundingBox.height,
        //         width: result.boundingBox.width,
        //         height: result.boundingBox.height
        //     ),
        //     confidence: Double(result.confidence)
        // )
    }
}
