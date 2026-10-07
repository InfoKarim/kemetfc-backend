//
//  VideoFramePersonDetector.swift
//  KemetFCTracker
//
//  Finds the people on ONE still frame of a stored video, for SELECT PLAYER IN VIDEO. It uses the same Apple
//  Vision request the live camera already uses (VNDetectHumanRectanglesRequest, through the same version
//  constant), applied to an upright CGImage. It answers "where are people", never "who is this": there is no
//  face detection, no face recognition and no biometric feature anywhere.
//

import AVFoundation
import CoreGraphics
import Foundation
import Vision

enum VideoFramePersonDetector {
    static let detectorVersion = PlayerDetector.detectorVersion

    /// People on the frame as normalized, top-left-origin boxes.
    static func people(in image: CGImage) throws -> [VideoPersonBox] {
        let request = VNDetectHumanRectanglesRequest()
        request.upperBodyOnly = false
        let handler = VNImageRequestHandler(cgImage: image, orientation: .up, options: [:])
        try handler.perform([request])
        return (request.results ?? []).map { observation in
            let b = observation.boundingBox          // Vision: bottom-left origin
            return VideoPersonBox(box: CGRect(x: b.origin.x, y: 1 - b.origin.y - b.height, width: b.width, height: b.height),
                                  confidence: Double(observation.confidence))
        }
    }

    /// Mean brightness (0...255) and a sharpness measure (variance of a Laplacian) of a frame, from a small copy.
    static func quality(of image: CGImage) -> (brightness: Double, sharpness: Double) {
        let width = 96, height = 96
        var pixels = [UInt8](repeating: 0, count: width * height)
        guard let context = CGContext(data: &pixels, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width,
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return (0, 0) }
        context.interpolationQuality = .medium
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        let mean = pixels.reduce(0.0) { $0 + Double($1) } / Double(pixels.count)
        var laplacian: [Double] = []
        for y in 1..<(height - 1) {
            for x in 1..<(width - 1) {
                let c = Double(pixels[y * width + x]) * 4
                let sum = Double(pixels[(y - 1) * width + x]) + Double(pixels[(y + 1) * width + x]) + Double(pixels[y * width + x - 1]) + Double(pixels[y * width + x + 1])
                laplacian.append(c - sum)
            }
        }
        let lm = laplacian.reduce(0, +) / Double(laplacian.count)
        let variance = laplacian.reduce(0) { $0 + ($1 - lm) * ($1 - lm) } / Double(laplacian.count)
        return (mean, variance)
    }
}
