//
//  CaptureFormatChooser.swift
//  KemetFCTracker
//
//  Chooses the camera format. Foundation-only (a plain value type per format) so the
//  choice is unit-testable off-device.
//
//  WHY: the old rule picked the format with the HIGHEST max frame rate — on current
//  iPhones a 240 fps slow-motion format — and then ran it at 60 fps. Physical-device
//  recordings from that setup contained ~30 frames (0.12 s at a 240 fps timebase) even
//  though the session ran for a minute and the tracker kept working. High-frame-rate
//  formats come with restrictions (no cinematic stabilization, tighter limits when a
//  movie file output and a video data output share the session), and nothing here
//  needs more than 60 fps. So: prefer the plainest 1080p format that can do 60 fps and
//  use a high-frame-rate format only if nothing else exists.
//

import Foundation

public struct CaptureFormatInfo: Equatable, Sendable {
    public var width: Int
    public var height: Int
    public var maxFrameRate: Double

    public init(width: Int, height: Int, maxFrameRate: Double) {
        self.width = width
        self.height = height
        self.maxFrameRate = maxFrameRate
    }

    var pixels: Int { width * height }
}

public enum CaptureFormatChooser {
    public static let targetFrameRate = 60.0
    /// A format whose top rate is above this is a high-frame-rate (slow-motion) format.
    static let highFrameRateThreshold = 60.5

    public struct Choice: Equatable, Sendable {
        public var index: Int
        public var frameRate: Double
        public var isHighFrameRateFormat: Bool
    }

    /// - Returns: nil if no format is at least 1080p.
    public static func choose(from formats: [CaptureFormatInfo], minWidth: Int = 1920, minHeight: Int = 1080) -> Choice? {
        let eligible = formats.enumerated().filter { $0.element.width >= minWidth && $0.element.height >= minHeight }
        guard !eligible.isEmpty else { return nil }

        func choice(_ item: (offset: Int, element: CaptureFormatInfo)) -> Choice {
            Choice(
                index: item.offset,
                frameRate: min(targetFrameRate, item.element.maxFrameRate),
                isHighFrameRateFormat: item.element.maxFrameRate > highFrameRateThreshold
            )
        }
        func plain60(_ info: CaptureFormatInfo) -> Bool {
            info.maxFrameRate >= targetFrameRate && info.maxFrameRate <= highFrameRateThreshold
        }
        func cheapest(_ items: [(offset: Int, element: CaptureFormatInfo)]) -> (offset: Int, element: CaptureFormatInfo)? {
            items.min { ($0.element.pixels, $0.offset) < ($1.element.pixels, $1.offset) }
        }

        // 1. Exactly 1080p that can do 60 fps and is not a high-frame-rate format.
        if let exact = cheapest(eligible.filter { $0.element.width == 1920 && $0.element.height == 1080 && plain60($0.element) }) {
            return choice(exact)
        }
        // 2. Any plain format that can do 60 fps (smallest picture first: cheapest to encode and analyse).
        if let plain = cheapest(eligible.filter { plain60($0.element) }) { return choice(plain) }
        // 3. No 60 fps format: the fastest non-high-frame-rate one (then the smallest picture).
        let slower = eligible.filter { $0.element.maxFrameRate <= highFrameRateThreshold }
        if let best = slower.max(by: { ($0.element.maxFrameRate, -$0.element.pixels, -$0.offset) < ($1.element.maxFrameRate, -$1.element.pixels, -$1.offset) }) {
            return choice(best)
        }
        // 4. Only high-frame-rate formats exist: the one with the lowest top rate, then the smallest picture.
        let fast = eligible.min { ($0.element.maxFrameRate, $0.element.pixels, $0.offset) < ($1.element.maxFrameRate, $1.element.pixels, $1.offset) }
        return fast.map(choice)
    }
}
