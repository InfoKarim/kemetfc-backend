import CoreGraphics
import Foundation

// Pure checks of VideoTargetGeometry / VideoTargetPicker / VideoFrameSuggestion / VideoTargetSelection, compiled
// from the app's own source by tests/test_ios_target_selection.py. No app, no phone, no video.

var failures: [String] = []
func check(_ c: Bool, _ m: String) { if !c { failures.append(m) } }
func near(_ a: CGFloat, _ b: CGFloat, _ eps: CGFloat = 1e-6) -> Bool { abs(a - b) <= eps }
func near(_ p: CGPoint?, _ x: CGFloat, _ y: CGFloat, _ eps: CGFloat = 1e-6) -> Bool {
    guard let p else { return false }
    return near(p.x, x, eps) && near(p.y, y, eps)
}
typealias G = VideoTargetGeometry
typealias P = VideoTargetPicker

// ---- 4. portrait video in a portrait view (an iPhone video on an iPhone screen) -------------------------------
let portraitVideo = CGSize(width: 1080, height: 1920)          // 9:16
let portraitView = CGSize(width: 400, height: 800)             // 1:2, taller than the video's shape
do {
    let rect = G.contentRect(contentSize: portraitVideo, viewSize: portraitView)!
    check(near(rect.width, 400) && near(rect.height, 711.1111111, 1e-4), "portrait video is pillar-free, letterboxed top and bottom: \(rect)")
    check(near(rect.minY, (800 - rect.height) / 2), "centred vertically")
    check(near(G.normalizedPoint(forTap: CGPoint(x: 200, y: 400), viewSize: portraitView, contentSize: portraitVideo), 0.5, 0.5), "the centre of the view is the centre of the video")
    check(near(G.normalizedPoint(forTap: CGPoint(x: 0, y: rect.minY), viewSize: portraitView, contentSize: portraitVideo), 0, 0), "the video's top-left corner")
    check(near(G.normalizedPoint(forTap: CGPoint(x: 399.9999, y: rect.maxY - 0.0001), viewSize: portraitView, contentSize: portraitVideo), 1, 1, 1e-3), "the video's bottom-right corner")
    // ---- 8. letterboxing: a tap in the black band is NOT a tap on the video, and is never clamped onto it
    check(G.normalizedPoint(forTap: CGPoint(x: 200, y: 10), viewSize: portraitView, contentSize: portraitVideo) == nil, "top letterbox band -> nil")
    check(G.normalizedPoint(forTap: CGPoint(x: 200, y: 795), viewSize: portraitView, contentSize: portraitVideo) == nil, "bottom letterbox band -> nil")
    check(G.normalizedPoint(forTap: CGPoint(x: -5, y: 400), viewSize: portraitView, contentSize: portraitVideo) == nil, "outside the view -> nil")
}

// ---- 5. landscape video in a portrait view ---------------------------------------------------------------------
do {
    let video = CGSize(width: 1920, height: 1080)
    let view = CGSize(width: 400, height: 800)
    let rect = G.contentRect(contentSize: video, viewSize: view)!
    check(near(rect.width, 400) && near(rect.height, 225) && near(rect.minY, 287.5), "landscape video is a wide band in the middle: \(rect)")
    check(near(G.normalizedPoint(forTap: CGPoint(x: 100, y: 287.5 + 56.25), viewSize: view, contentSize: video), 0.25, 0.25), "a quarter in, a quarter down")
    check(G.normalizedPoint(forTap: CGPoint(x: 100, y: 100), viewSize: view, contentSize: video) == nil, "above the band -> nil")
    // landscape video in a landscape view: pillarbox only when the view is wider than the video
    let wide = CGSize(width: 1000, height: 400)
    let r2 = G.contentRect(contentSize: video, viewSize: wide)!
    check(near(r2.height, 400) && near(r2.width, 711.1111111, 1e-4) && near(r2.minX, (1000 - r2.width) / 2), "pillarbox: \(r2)")
    check(G.normalizedPoint(forTap: CGPoint(x: 50, y: 200), viewSize: wide, contentSize: video) == nil, "left pillar band -> nil")
}

// ---- 6. rotated video metadata ---------------------------------------------------------------------------------------
do {
    // an iPhone portrait video is stored 1920x1080 with a 90 degree rotation; the viewer sees 1080x1920
    let stored = CGSize(width: 1920, height: 1080)
    let rotate90 = CGAffineTransform(a: 0, b: 1, c: -1, d: 0, tx: 1080, ty: 0)
    let rotateMinus90 = CGAffineTransform(a: 0, b: -1, c: 1, d: 0, tx: 0, ty: 1920)
    let rotate180 = CGAffineTransform(a: -1, b: 0, c: 0, d: -1, tx: 1920, ty: 1080)
    check(G.uprightSize(natural: stored, preferredTransform: rotate90) == CGSize(width: 1080, height: 1920), "90 degrees swaps width and height")
    check(G.uprightSize(natural: stored, preferredTransform: rotateMinus90) == CGSize(width: 1080, height: 1920), "-90 degrees swaps width and height")
    check(G.uprightSize(natural: stored, preferredTransform: rotate180) == CGSize(width: 1920, height: 1080), "180 degrees keeps the shape")
    check(G.uprightSize(natural: stored, preferredTransform: .identity) == CGSize(width: 1920, height: 1080), "no rotation keeps the shape")
    // the mapping uses the upright size, so the same tap lands on the same place of the same picture
    let view = CGSize(width: 400, height: 800)
    let upright = G.uprightSize(natural: stored, preferredTransform: rotate90)
    let wrong = G.normalizedPoint(forTap: CGPoint(x: 200, y: 250), viewSize: view, contentSize: stored)        // ignoring the rotation
    let right = G.normalizedPoint(forTap: CGPoint(x: 200, y: 250), viewSize: view, contentSize: upright)
    check(wrong == nil && right != nil, "ignoring the rotation would put this tap in the black band; using it puts it on the picture")
}

// ---- 7. aspect fit vs fill, 9. safe areas -----------------------------------------------------------------------
do {
    let video = CGSize(width: 1080, height: 1920), view = CGSize(width: 400, height: 400)
    let fit = G.contentRect(contentSize: video, viewSize: view, fit: .aspectFit)!
    let fill = G.contentRect(contentSize: video, viewSize: view, fit: .aspectFill)!
    check(near(fit.height, 400) && near(fit.width, 225), "fit keeps everything: \(fit)")
    check(near(fill.width, 400) && near(fill.height, 711.1111111, 1e-4) && near(fill.minY, (400 - fill.height) / 2), "fill covers the view and crops: \(fill)")
    check(near(G.normalizedPoint(forTap: CGPoint(x: 200, y: 200), viewSize: view, contentSize: video, fit: .aspectFill), 0.5, 0.5), "fill centre")
    check(near(G.normalizedPoint(forTap: CGPoint(x: 0, y: 0), viewSize: view, contentSize: video, fit: .aspectFill), 0, 0.5 - 200 / 711.1111111, 1e-4), "fill top-left corner sees the cropped middle")
    // safe areas: the video is laid out inside the insets, taps in an inset are not on the video
    let insets = G.Insets(top: 60, left: 0, bottom: 34, right: 0)
    let tall = CGSize(width: 400, height: 800)
    let r = G.contentRect(contentSize: video, viewSize: tall, insets: insets)!
    check(r.minY >= 60 - 1e-9 && r.maxY <= 800 - 34 + 1e-9, "the picture stays inside the safe area: \(r)")
    check(G.normalizedPoint(forTap: CGPoint(x: 200, y: 30), viewSize: tall, contentSize: video, insets: insets) == nil, "a tap in the top inset is not on the video")
    check(G.normalizedPoint(forTap: CGPoint(x: 200, y: 790), viewSize: tall, contentSize: video, insets: insets) == nil, "a tap in the bottom inset is not on the video")
    check(near(G.normalizedPoint(forTap: CGPoint(x: r.midX, y: r.midY), viewSize: tall, contentSize: video, insets: insets), 0.5, 0.5), "the centre is still the centre")
    // degenerate sizes never crash or invent a point
    check(G.contentRect(contentSize: .zero, viewSize: tall) == nil && G.contentRect(contentSize: video, viewSize: .zero) == nil, "zero sizes -> nil")
    check(G.normalizedPoint(forTap: CGPoint(x: 1, y: 1), viewSize: tall, contentSize: .zero) == nil, "zero content -> nil")
}

// ---- round trip: a box drawn from a normalized rect is the one the tap maps back into -------------------------------
do {
    let video = CGSize(width: 1080, height: 1920), view = CGSize(width: 390, height: 700)
    let norm = CGRect(x: 0.30, y: 0.40, width: 0.20, height: 0.30)
    let drawn = G.viewRect(forNormalized: norm, viewSize: view, contentSize: video)!
    let back = G.normalizedPoint(forTap: CGPoint(x: drawn.midX, y: drawn.midY), viewSize: view, contentSize: video)!
    check(near(back.x, norm.midX) && near(back.y, norm.midY), "drawing and tapping agree")
    let corner = G.normalizedPoint(forTap: CGPoint(x: drawn.minX + 0.01, y: drawn.minY + 0.01), viewSize: view, contentSize: video)!
    check(norm.contains(corner), "just inside the drawn box is inside the normalized box")
}

// ---- 1-3. the tap picks the person it is ON ---------------------------------------------------------------------------
let adult = VideoPersonBox(box: CGRect(x: 0.10, y: 0.20, width: 0.20, height: 0.60), confidence: 0.9)
let child = VideoPersonBox(box: CGRect(x: 0.12, y: 0.45, width: 0.10, height: 0.30), confidence: 0.8)
let other = VideoPersonBox(box: CGRect(x: 0.60, y: 0.30, width: 0.15, height: 0.50), confidence: 0.7)
let people = [adult, child, other]
check(P.personIndex(at: CGPoint(x: 0.67, y: 0.55), among: people) == 2, "1. a tap inside the right person selects them")
check(P.personIndex(at: CGPoint(x: 0.20, y: 0.25), among: people) == 0, "1. the adult above the child")
check(P.personIndex(at: CGPoint(x: 0.45, y: 0.50), among: people) == nil, "2. a tap outside everyone selects no one")
check(P.personIndex(at: CGPoint(x: 0.31, y: 0.50), among: people) == nil, "2. close to a person is still no one (nothing is guessed)")
check(P.personIndex(at: CGPoint(x: 1.2, y: 0.5), among: people) == nil && P.personIndex(at: CGPoint(x: 0.5, y: 0.5), among: []) == nil, "2. off the frame / nobody detected")
check(P.personIndex(at: CGPoint(x: 0.17, y: 0.60), among: people) == 1, "3. overlap: the child's own centre picks the child")
check(P.personIndex(at: CGPoint(x: 0.17, y: 0.30), among: people) == 0, "3. overlap: the adult's body above the child is the adult")
let twin1 = VideoPersonBox(box: CGRect(x: 0.2, y: 0.2, width: 0.2, height: 0.4), confidence: 0.5)
let twin2 = VideoPersonBox(box: CGRect(x: 0.2, y: 0.2, width: 0.2, height: 0.4), confidence: 0.9)
check(P.personIndex(at: CGPoint(x: 0.3, y: 0.4), among: [twin1, twin2]) == 1, "3. identical boxes: the higher confidence")
check(P.personIndex(at: CGPoint(x: 0.3, y: 0.4), among: [twin2, twin1]) == 0, "3. identical boxes: confidence beats order")
let same1 = VideoPersonBox(box: CGRect(x: 0.2, y: 0.2, width: 0.2, height: 0.4), confidence: 0.7)
check(P.personIndex(at: CGPoint(x: 0.3, y: 0.4), among: [same1, same1]) == 0, "3. a full tie: the lower index")
check(Set((0..<20).map { _ in P.personIndex(at: CGPoint(x: 0.17, y: 0.60), among: people) }) == [1], "3. deterministic")
check(P.noPlayerMessage == "No player detected at that point. Tap directly on the player.", "the message the coach sees")

// ---- frame suggestion ---------------------------------------------------------------------------------------------------
do {
    let person = VideoPersonBox(box: CGRect(x: 0.3, y: 0.3, width: 0.2, height: 0.5), confidence: 0.9)
    func cand(_ t: Double, people: [VideoPersonBox] = [person], bright: Double = 120, sharp: Double = 100) -> VideoFrameSuggestion.Candidate {
        .init(timeSeconds: t, people: people, brightness: bright, sharpness: sharp)
    }
    check(VideoFrameSuggestion.best([]) == nil, "no frames, no suggestion")
    check(VideoFrameSuggestion.best([cand(0, people: [])]) == nil, "an empty frame is never suggested")
    check(VideoFrameSuggestion.best([cand(0, bright: 5)]) == nil, "a black frame is never suggested")
    check(VideoFrameSuggestion.best([cand(0, people: [VideoPersonBox(box: CGRect(x: 0.3, y: 0.3, width: 0.02, height: 0.03), confidence: 0.95)])]) == nil, "a tiny person is not a useful target")
    check(VideoFrameSuggestion.best([cand(0, people: [VideoPersonBox(box: person.box, confidence: 0.3)])]) == nil, "a low-confidence detection is not offered")
    let picked = VideoFrameSuggestion.best([cand(0, people: []), cand(1, bright: 5), cand(2, sharp: 10), cand(3), cand(4), cand(5)])
    check(picked?.timeSeconds == 3, "the first good frame wins over transitions, blur and frame 0: \(String(describing: picked?.timeSeconds))")
    let crowd = VideoFrameSuggestion.best([cand(3), cand(4, people: [person, person])])
    check(crowd?.timeSeconds == 4, "more usable people is better")
}

// ---- the stored selection ------------------------------------------------------------------------------------------------
do {
    let selection = VideoTargetSelection(label: "TARGET-1", source: VideoTargetSelection.coachTapSource, timeSeconds: 2.5, tapX: 0.67, tapY: 0.55,
                                         boxX: 0.6, boxY: 0.3, boxWidth: 0.15, boxHeight: 0.5, detectionConfidence: 0.7,
                                         detectorVersion: "apple-vision-VNDetectHumanRectanglesRequest-ios13", videoSha256: "ab12", confirmedAt: Date(timeIntervalSince1970: 1_700_000_000))
    let data = try! JSONEncoder().encode(selection)
    let back = try! JSONDecoder().decode(VideoTargetSelection.self, from: data)
    check(back == selection, "a selection survives encoding")
    let text = String(data: data, encoding: .utf8)!
    check(!text.contains("playerId") && !text.contains("PILOT") && !text.contains("face"), "a selection carries no roster id and no face or biometric field")
    check(selection.source == "COACH_TAP", "only a coach tap makes a selection")
    typealias S = VideoTargetSelectionState
    check(!S.choosing.canConfirm && S.selected(index: 0).canConfirm && !S.confirmed.canConfirm, "confirm only after a person is selected")
    check(!S.choosing.analysisMayStart && !S.selected(index: 0).analysisMayStart && S.confirmed.analysisMayStart, "analysis may start only after confirmation")
}

if failures.isEmpty { print("ALL CHECKS PASSED") } else { failures.forEach { print("FAIL:", $0) }; exit(1) }
