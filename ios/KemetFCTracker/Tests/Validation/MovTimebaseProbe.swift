//
//  MovTimebaseProbe.swift
//  KemetFCTrackerTests
//
//  A READ-ONLY offline inspector of an existing .mov file's own recorded
//  video timing, using AVURLAsset/AVAssetReader (2026-09-28, offline MOV
//  timebase probe). This is an OBSERVER, not a synchronization guesser: it
//  never modifies the movie, never touches recording/camera code, and never
//  automatically produces a `TimestampMapping` from a .mov alone — see
//  `MovTimebaseComparator.swift` for the explicitly-labeled "RAW TIMEBASE
//  COMPARISON" that is the closest this gets to combining it with capture-
//  side data, and even that never becomes a validated mapping by itself.
//

import AVFoundation
import Foundation

/// What one `.mov` file's own video track reports about its recorded
/// timing. Every field is nil when it genuinely could not be determined
/// (e.g. no video track) — never a fabricated zero.
public struct MovTimebaseProbeResult: Equatable, Sendable {
    public var filePath: String
    public var assetDurationSeconds: Double?
    public var videoTrackCount: Int
    public var firstVideoSamplePtsSeconds: Double?
    public var lastVideoSamplePtsSeconds: Double?
    public var firstSampleDurationSeconds: Double?
    public var trackNaturalTimeScale: Int32?

    public init(
        filePath: String, assetDurationSeconds: Double?, videoTrackCount: Int,
        firstVideoSamplePtsSeconds: Double?, lastVideoSamplePtsSeconds: Double?,
        firstSampleDurationSeconds: Double?, trackNaturalTimeScale: Int32?
    ) {
        self.filePath = filePath
        self.assetDurationSeconds = assetDurationSeconds
        self.videoTrackCount = videoTrackCount
        self.firstVideoSamplePtsSeconds = firstVideoSamplePtsSeconds
        self.lastVideoSamplePtsSeconds = lastVideoSamplePtsSeconds
        self.firstSampleDurationSeconds = firstSampleDurationSeconds
        self.trackNaturalTimeScale = trackNaturalTimeScale
    }

    /// A strict, narrow check: is the first sample's PTS essentially
    /// exactly zero. nil when the first PTS itself is unknown.
    public var firstPtsIsApproximatelyZero: Bool? {
        firstVideoSamplePtsSeconds.map { abs($0) < 0.001 }
    }

    /// A LOOSER heuristic: is the first sample's PTS small enough to be
    /// consistent with a file-relative clock (as opposed to an absolute
    /// host-uptime-style clock, which would typically be a much larger
    /// number — e.g. thousands of seconds since device boot). THIS ALONE
    /// PROVES NOTHING about correspondence with the live capture clock —
    /// it is one observation, not a conclusion. A small-but-nonzero first
    /// PTS is still consistent with either domain.
    public var appearsNormalizedToFileRelativeTimeline: Bool? {
        firstVideoSamplePtsSeconds.map { abs($0) < 1.0 }
    }
}

public enum MovTimebaseProbe {
    public enum ProbeError: Error, Equatable {
        case noVideoTrack
        case readerCreationFailed(String)
    }

    /// Inspects `url` read-only. Never writes to, moves, or deletes the
    /// file. Reads COMPRESSED samples only (no `outputSettings`, i.e. no
    /// pixel-format decode) — this only needs sample TIMING metadata, never
    /// the actual frame image data, so it stays fast even for a large file.
    public static func inspect(url: URL) async throws -> MovTimebaseProbeResult {
        let asset = AVURLAsset(url: url)
        let duration = try? await asset.load(.duration)
        let videoTracks = (try? await asset.loadTracks(withMediaType: .video)) ?? []

        guard let track = videoTracks.first else {
            return MovTimebaseProbeResult(
                filePath: url.path, assetDurationSeconds: duration.map { CMTimeGetSeconds($0) },
                videoTrackCount: 0, firstVideoSamplePtsSeconds: nil, lastVideoSamplePtsSeconds: nil,
                firstSampleDurationSeconds: nil, trackNaturalTimeScale: nil
            )
        }

        let naturalTimeScale = try? await track.load(.naturalTimeScale)

        let reader = try AVAssetReader(asset: asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil) // compressed samples — timing only
        guard reader.canAdd(output) else {
            throw ProbeError.readerCreationFailed("AVAssetReader could not add a track output for the video track")
        }
        reader.add(output)
        guard reader.startReading() else {
            throw ProbeError.readerCreationFailed(reader.error?.localizedDescription ?? "unknown AVAssetReader failure")
        }

        var firstPts: CMTime?
        var lastPts: CMTime?
        var firstDuration: CMTime?
        while let sampleBuffer = output.copyNextSampleBuffer() {
            let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
            if firstPts == nil {
                firstPts = pts
                firstDuration = CMSampleBufferGetDuration(sampleBuffer)
            }
            lastPts = pts
        }

        return MovTimebaseProbeResult(
            filePath: url.path,
            assetDurationSeconds: duration.map { CMTimeGetSeconds($0) },
            videoTrackCount: videoTracks.count,
            firstVideoSamplePtsSeconds: firstPts.map { CMTimeGetSeconds($0) },
            lastVideoSamplePtsSeconds: lastPts.map { CMTimeGetSeconds($0) },
            firstSampleDurationSeconds: firstDuration.map { CMTimeGetSeconds($0) },
            trackNaturalTimeScale: naturalTimeScale
        )
    }
}
