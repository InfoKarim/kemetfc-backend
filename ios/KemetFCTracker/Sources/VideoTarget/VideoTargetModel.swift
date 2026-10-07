//
//  VideoTargetModel.swift
//  KemetFCTracker
//
//  SELECT PLAYER IN VIDEO: the rules, as plain values, so every one is testable without a screen, a camera or a
//  video. Foundation + CoreGraphics only.
//
//  The roster player says whose assessment a video is. It does not say which visible person in the video is
//  that player: the coach taps them. A tap is a point; a detection is a box. This file turns the one into the
//  other or refuses. There is no face recognition and no biometric identity anywhere: positions and boxes only.
//
//  Coordinates: "normalized" means 0...1 in the UPRIGHT video frame (rotation already applied), origin top-left,
//  the same convention the Mac AI pipeline uses, so a selection means the same thing on both sides.
//

import CoreGraphics
import Foundation

// BEGIN VideoTargetModel

/// Where a displayed video frame sits inside the view that shows it, and the maps between the two.
enum VideoTargetGeometry {
    enum Fit { case aspectFit, aspectFill }

    struct Insets: Equatable {
        var top: CGFloat = 0, left: CGFloat = 0, bottom: CGFloat = 0, right: CGFloat = 0
        static let zero = Insets()
    }

    /// The video's UPRIGHT size: its stored size with the track's own rotation applied. A phone video stored
    /// 1920x1080 with a 90 degree transform is 1080 wide and 1920 tall to the viewer.
    static func uprightSize(natural: CGSize, preferredTransform: CGAffineTransform) -> CGSize {
        let rect = CGRect(origin: .zero, size: natural).applying(preferredTransform)
        return CGSize(width: abs(rect.width).rounded(), height: abs(rect.height).rounded())
    }

    /// The rectangle the video frame occupies, in the view's own coordinates. For aspect-fit the unused bands
    /// are the letterbox / pillarbox; the frame is centred in the area left after the safe-area insets.
    static func contentRect(contentSize: CGSize, viewSize: CGSize, insets: Insets = .zero, fit: Fit = .aspectFit) -> CGRect? {
        let area = CGRect(x: insets.left, y: insets.top,
                          width: viewSize.width - insets.left - insets.right,
                          height: viewSize.height - insets.top - insets.bottom)
        guard contentSize.width > 0, contentSize.height > 0, area.width > 0, area.height > 0 else { return nil }
        let scaleX = area.width / contentSize.width, scaleY = area.height / contentSize.height
        let scale = fit == .aspectFit ? min(scaleX, scaleY) : max(scaleX, scaleY)
        let size = CGSize(width: contentSize.width * scale, height: contentSize.height * scale)
        return CGRect(x: area.midX - size.width / 2, y: area.midY - size.height / 2, width: size.width, height: size.height)
    }

    /// A tap in the view -> a normalized point in the upright video frame. nil when the tap is not on the video
    /// at all (in a letterbox band, in a safe-area inset, or outside the view): nothing is clamped or guessed.
    static func normalizedPoint(forTap tap: CGPoint, viewSize: CGSize, contentSize: CGSize,
                                insets: Insets = .zero, fit: Fit = .aspectFit) -> CGPoint? {
        guard let rect = contentRect(contentSize: contentSize, viewSize: viewSize, insets: insets, fit: fit) else { return nil }
        let visible = CGRect(x: insets.left, y: insets.top,
                             width: viewSize.width - insets.left - insets.right, height: viewSize.height - insets.top - insets.bottom)
        guard visible.contains(tap), rect.contains(tap) else { return nil }
        return CGPoint(x: (tap.x - rect.minX) / rect.width, y: (tap.y - rect.minY) / rect.height)
    }

    /// The inverse, for drawing a box on top of the video.
    static func viewRect(forNormalized box: CGRect, viewSize: CGSize, contentSize: CGSize,
                         insets: Insets = .zero, fit: Fit = .aspectFit) -> CGRect? {
        guard let rect = contentRect(contentSize: contentSize, viewSize: viewSize, insets: insets, fit: fit) else { return nil }
        return CGRect(x: rect.minX + box.minX * rect.width, y: rect.minY + box.minY * rect.height,
                      width: box.width * rect.width, height: box.height * rect.height)
    }
}

/// One person found on a frame. `box` is normalized, top-left origin.
struct VideoPersonBox: Equatable {
    var box: CGRect
    var confidence: Double
}

enum VideoTargetPicker {
    static let noPlayerMessage = "No player detected at that point. Tap directly on the player."

    /// The index of the person the tap is ON, or nil. Never the nearest person. If several boxes contain the
    /// tap, in order: the box whose centre is closest to the tap measured in that box's own size, then the
    /// smaller box, then the higher confidence, then the lower index. Identical to the Mac pipeline's rule.
    static func personIndex(at tap: CGPoint, among people: [VideoPersonBox]) -> Int? {
        guard (0...1).contains(tap.x), (0...1).contains(tap.y) else { return nil }
        let hits = people.enumerated().filter { $0.element.box.contains(tap) }
        func key(_ item: (offset: Int, element: VideoPersonBox)) -> (Double, Double, Double, Int) {
            let b = item.element.box
            let w = max(Double(b.width), 1e-9), h = max(Double(b.height), 1e-9)
            let dx = (Double(tap.x) - Double(b.midX)) / w, dy = (Double(tap.y) - Double(b.midY)) / h
            let offset = (dx * dx + dy * dy).squareRoot()
            return ((offset * 1e9).rounded() / 1e9, ((w * h) * 1e9).rounded() / 1e9, -item.element.confidence, item.offset)
        }
        return hits.min { a, b in
            let ka = key(a), kb = key(b)
            if ka.0 != kb.0 { return ka.0 < kb.0 }
            if ka.1 != kb.1 { return ka.1 < kb.1 }
            if ka.2 != kb.2 { return ka.2 < kb.2 }
            return ka.3 < kb.3
        }?.offset
    }
}

/// Is a frame worth showing the coach? Not frame 0 by default: people visible, plausible detections, not dark,
/// not blurred, not a transition.
enum VideoFrameSuggestion {
    struct Candidate: Equatable {
        var timeSeconds: Double
        var people: [VideoPersonBox]
        var brightness: Double       // 0...255 mean luma
        var sharpness: Double        // larger = sharper (variance of a Laplacian)
    }

    static let minimumConfidence = 0.5
    static let minimumBoxHeight = 0.12
    static let minimumBrightness = 25.0

    /// The usable people on a frame, or nil if the frame should not be offered.
    static func usablePeople(_ c: Candidate, medianSharpness: Double) -> [VideoPersonBox]? {
        guard c.brightness >= minimumBrightness else { return nil }
        if medianSharpness > 0, c.sharpness < 0.5 * medianSharpness { return nil }
        let people = c.people.filter { $0.confidence >= minimumConfidence && Double($0.box.height) >= minimumBoxHeight }
        return people.isEmpty ? nil : people
    }

    /// The best frame among the candidates (highest confidence x sharpness x number of people), or nil.
    static func best(_ candidates: [Candidate]) -> Candidate? {
        let sharp = candidates.map(\.sharpness).sorted()
        let median = sharp.isEmpty ? 0 : sharp[sharp.count / 2]
        var best: (score: Double, candidate: Candidate)?
        for c in candidates {
            guard let people = usablePeople(c, medianSharpness: median) else { continue }
            let meanConfidence = people.map(\.confidence).reduce(0, +) / Double(people.count)
            let sharpPart = median > 0 ? min(c.sharpness / median, 2) : 1
            let score = meanConfidence * sharpPart * Double(min(people.count, 3))
            if best == nil || score > best!.score || (score == best!.score && c.timeSeconds < best!.candidate.timeSeconds) {
                best = (score, c)
            }
        }
        return best?.candidate
    }
}

/// The coach's confirmed choice of player in one video. A temporary tracking identity for that video: it is not
/// a roster player id and not a face or body identity.
public struct VideoTargetSelection: Codable, Equatable {
    public var label: String
    /// "COACH_TAP"
    public var source: String
    public var timeSeconds: Double
    public var tapX: Double
    public var tapY: Double
    public var boxX: Double
    public var boxY: Double
    public var boxWidth: Double
    public var boxHeight: Double
    public var detectionConfidence: Double
    public var detectorVersion: String
    /// The SHA-256 of the video file this selection was made on, when known, so a selection is never applied to a
    /// different file.
    public var videoSha256: String?
    public var confirmedAt: Date

    public static let coachTapSource = "COACH_TAP"
}

/// What the screen is doing. The AI never starts from here: confirming only stores the selection.
enum VideoTargetSelectionState: Equatable {
    case choosing
    case selected(index: Int)
    case confirmed

    var canConfirm: Bool { if case .selected = self { return true } else { return false } }
    var analysisMayStart: Bool { self == .confirmed }
}
// END VideoTargetModel
