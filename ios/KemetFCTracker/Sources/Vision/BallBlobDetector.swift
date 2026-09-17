//
//  BallBlobDetector.swift
//  KemetFCTracker
//
//  STATUS: Compiled AND run with the real Swift 6.3.3 compiler on this
//  machine (`swiftc`, standalone executable — see FramingCalculator.swift's
//  header for why not `swift test`). Pure Foundation/CoreGraphics, no
//  CoreImage/Vision/UIKit dependency, so this is possible outside Xcode.
//  Result when last run: see Tests/BallBlobDetectorTests.swift header.
//
//  Phase 2 fix for a real, previously-open gap: ClassicalCVBallDetector's
//  brightestRoughlyCircularBlob() returned `nil` unconditionally — a
//  complete stub, not a working (if weak) heuristic. This file is the
//  actual, non-fabricated algorithm: still explicitly a classical-CV
//  heuristic (spec: "Do not fabricate a trained model"), still weak
//  compared to a trained detector, but genuinely does something —
//  connected-component search for a small, roughly-circular, bright,
//  color-balanced (non-green, non-jersey-colored) blob.
//
//  Algorithm (documented, not learned):
//   1. Per-pixel candidate test: bright (luminance above threshold) AND
//      color-balanced (R/G/B channels close to each other) — a soccer
//      ball is typically white/grey/bright and NOT saturated, whereas
//      grass is green-dominant and jerseys are usually a single
//      saturated hue. This deliberately rejects colored jerseys and
//      grass, and (like any classical heuristic) will also falsely
//      accept other bright achromatic objects (cones, chalk lines,
//      socks) — documented as a known weakness, not hidden.
//   2. Flood-fill connected-component labeling over the candidate mask
//      (iterative, not recursive, to avoid stack depth issues on a
//      populated frame).
//   3. Each component is scored on: area fraction of the frame (must be
//      within a plausible ball-at-typical-assessment-distance range),
//      aspect ratio of its bounding box (must be roughly square), and
//      fill ratio (pixel count / bounding-box area — a real circle
//      inscribed in its bounding square fills ~78.5% of it; an
//      elongated line or irregular blob fills much less). The
//      highest-fill-ratio component passing both gates wins.
//

import CoreGraphics
import Foundation

/// A raw RGBA8 pixel buffer, row-major, 4 bytes per pixel — the minimal
/// shape needed to run the blob search without depending on CoreImage/
/// CGImage (which can't be exercised outside Xcode in this environment).
/// ClassicalCVBallDetector converts a real CGImage into this shape.
public struct RGBAPixelBuffer {
    public let width: Int
    public let height: Int
    public let bytes: [UInt8]

    public init(width: Int, height: Int, bytes: [UInt8]) {
        precondition(bytes.count == width * height * 4, "byte count must match width*height*4 (RGBA8)")
        self.width = width
        self.height = height
        self.bytes = bytes
    }

    @inline(__always)
    func pixel(_ x: Int, _ y: Int) -> (r: Int, g: Int, b: Int) {
        let i = (y * width + x) * 4
        return (Int(bytes[i]), Int(bytes[i + 1]), Int(bytes[i + 2]))
    }
}

public enum BallBlobDetector {
    /// Tunable, documented thresholds — not learned weights.
    public struct Parameters {
        public var brightnessThreshold: Int
        public var channelBalanceTolerance: Int
        public var minAreaFraction: Double
        public var maxAreaFraction: Double
        public var minAspectRatio: Double // narrower side / wider side, so always <= 1
        public var minFillRatio: Double

        public init(
            brightnessThreshold: Int = 150,
            channelBalanceTolerance: Int = 28,
            minAreaFraction: Double = 0.0008,
            maxAreaFraction: Double = 0.05,
            minAspectRatio: Double = 0.65,
            minFillRatio: Double = 0.55
        ) {
            self.brightnessThreshold = brightnessThreshold
            self.channelBalanceTolerance = channelBalanceTolerance
            self.minAreaFraction = minAreaFraction
            self.maxAreaFraction = maxAreaFraction
            self.minAspectRatio = minAspectRatio
            self.minFillRatio = minFillRatio
        }

        public static let `default` = Parameters()
    }

    private struct Component {
        var minX = Int.max
        var minY = Int.max
        var maxX = Int.min
        var maxY = Int.min
        var pixelCount = 0

        mutating func include(_ x: Int, _ y: Int) {
            minX = min(minX, x)
            minY = min(minY, y)
            maxX = max(maxX, x)
            maxY = max(maxY, y)
            pixelCount += 1
        }
    }

    @inline(__always)
    private static func isCandidate(_ p: (r: Int, g: Int, b: Int), _ params: Parameters) -> Bool {
        let luminance = (p.r + p.g + p.b) / 3
        guard luminance >= params.brightnessThreshold else { return false }
        let maxChannel = max(p.r, p.g, p.b)
        let minChannel = min(p.r, p.g, p.b)
        return (maxChannel - minChannel) <= params.channelBalanceTolerance
    }

    /// Returns the bounding box (in `buffer`'s pixel coordinates, origin
    /// top-left) of the best-scoring candidate blob, or nil if nothing in
    /// the frame plausibly resembles a ball by this heuristic's
    /// deliberately narrow criteria.
    public static func findBrightRoughlyCircularBlob(
        in buffer: RGBAPixelBuffer,
        parameters: Parameters = .default
    ) -> CGRect? {
        guard buffer.width > 0, buffer.height > 0 else { return nil }

        var visited = [Bool](repeating: false, count: buffer.width * buffer.height)
        var best: Component?
        var bestFillRatio = 0.0
        let frameArea = Double(buffer.width * buffer.height)

        for y in 0..<buffer.height {
            for x in 0..<buffer.width {
                let idx = y * buffer.width + x
                if visited[idx] { continue }
                visited[idx] = true
                guard isCandidate(buffer.pixel(x, y), parameters) else { continue }

                // Iterative flood fill (4-connectivity) — recursive
                // flood fill risks a stack overflow on a large connected
                // bright region (e.g. an overexposed sky patch).
                var component = Component()
                var stack: [(Int, Int)] = [(x, y)]
                while let (cx, cy) = stack.popLast() {
                    component.include(cx, cy)
                    let neighbors = [(cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)]
                    for (nx, ny) in neighbors {
                        guard nx >= 0, nx < buffer.width, ny >= 0, ny < buffer.height else { continue }
                        let nIdx = ny * buffer.width + nx
                        if visited[nIdx] { continue }
                        visited[nIdx] = true
                        if isCandidate(buffer.pixel(nx, ny), parameters) {
                            stack.append((nx, ny))
                        }
                    }
                }

                let boxWidth = component.maxX - component.minX + 1
                let boxHeight = component.maxY - component.minY + 1
                let boxArea = Double(boxWidth * boxHeight)
                let areaFraction = boxArea / frameArea
                guard areaFraction >= parameters.minAreaFraction, areaFraction <= parameters.maxAreaFraction else { continue }

                let aspectRatio = Double(min(boxWidth, boxHeight)) / Double(max(boxWidth, boxHeight))
                guard aspectRatio >= parameters.minAspectRatio else { continue }

                let fillRatio = Double(component.pixelCount) / boxArea
                guard fillRatio >= parameters.minFillRatio else { continue }

                if fillRatio > bestFillRatio {
                    bestFillRatio = fillRatio
                    best = component
                }
            }
        }

        guard let winner = best else { return nil }
        return CGRect(
            x: winner.minX,
            y: winner.minY,
            width: winner.maxX - winner.minX + 1,
            height: winner.maxY - winner.minY + 1
        )
    }
}
