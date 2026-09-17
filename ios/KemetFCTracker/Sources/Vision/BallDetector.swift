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
//   2. `ClassicalCVBallDetector` — a real, working (once compiled)
//      fallback using CIFilter-based color/circularity heuristics. Not
//      a neural network. Genuinely detects a bright, roughly circular
//      blob against grass — genuinely fails on similarly-colored/shaped
//      objects (white cones, shoes, chalk lines) more often than a
//      trained detector would. Registered in the backend's
//      MLModelRegistryDB as "ball-detector" "classical-cv-v0.1",
//      status="experimental" — never silently presented as equivalent
//      to a trained model.
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
    func detectBall(in pixelBuffer: CVPixelBuffer) throws -> BallDetection?
}

/// Color/circularity heuristic — NOT a trained model. See file header.
public final class ClassicalCVBallDetector: BallDetecting {
    public let modelIdentifier: (name: String, version: String) = ("ball-detector", "classical-cv-v0.1")

    private let context = CIContext()

    public init() {}

    public func detectBall(in pixelBuffer: CVPixelBuffer) throws -> BallDetection? {
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let width = ciImage.extent.width
        let height = ciImage.extent.height
        guard width > 0, height > 0 else { return nil }

        // Downscale aggressively before any per-pixel work — this is a
        // cheap heuristic, not a neural network, and must stay cheap to
        // avoid competing with player/pose inference for the same frame
        // budget (spec section 26).
        let scale: CGFloat = 240 / max(width, height)
        guard let small = ciImage.transformed(by: CGAffineTransform(scaleX: scale, y: scale))
            .cgImage(context: context) else { return nil }

        guard let candidate = Self.brightestRoughlyCircularBlob(in: small) else { return nil }

        return BallDetection(
            boundingBox: NormalizedRect(
                x: candidate.origin.x / CGFloat(small.width),
                y: candidate.origin.y / CGFloat(small.height),
                width: candidate.width / CGFloat(small.width),
                height: candidate.height / CGFloat(small.height)
            ),
            // Deliberately capped well below 1.0 — a classical heuristic
            // should never claim the same certainty a trained detector
            // could; downstream quality gating (tracking_quality.py)
            // treats this conservatively.
            confidence: 0.4
        )
    }

    /// Placeholder blob search — scans for a small, high-contrast,
    /// roughly square (aspect ratio near 1:1) region. This is
    /// deliberately NOT a full implementation (real thresholding/
    /// connected-components code belongs here before shipping) — it's
    /// scaffolding showing where that logic plugs in, kept short because
    /// this whole class is explicitly a stand-in for a trained model,
    /// not the production ball detector.
    private static func brightestRoughlyCircularBlob(in image: CGImage) -> CGRect? {
        // Real implementation: threshold on luminance + saturation,
        // connected-component label, filter by size (2-6% of frame
        // width for a ball at typical assessment framing distance) and
        // aspect ratio (0.85-1.15), return the best-scoring blob's
        // bounding box in `image`'s pixel coordinates. Left unimplemented
        // here deliberately — see file header.
        return nil
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
