import AVFoundation
import CryptoKit
import Foundation

// Runs the REAL PendingAssessmentStore and VideoImportService (compiled from the app's source) on real video
// files on this Mac: a camera-style .mov, a rotated portrait .mp4, a file that is not a video, an empty file.
// Fixture paths come from the environment. No app, no phone, no network.

var failures: [String] = []
func check(_ condition: Bool, _ message: String) { if !condition { failures.append(message) } }

public struct KemetPlayerSummary { var playerId: String; var fullName: String }
extension JSONEncoder { static let kemet: JSONEncoder = { let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; return e }() }
extension JSONDecoder { static let kemet: JSONDecoder = { let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d }() }
public enum TrackingMode: String, Codable { case playerLock = "player_lock", ballTrack = "ball_track", smartSoccer = "smart_soccer" }

func sha(_ url: URL) -> String {
    SHA256.hash(data: try! Data(contentsOf: url)).map { String(format: "%02x", $0) }.joined()
}
func copyOf(_ url: URL, into dir: URL, as name: String) -> URL {
    let target = dir.appendingPathComponent(name)
    try! FileManager.default.copyItem(at: url, to: target)
    return target
}

@MainActor
func run() async {
    let env = ProcessInfo.processInfo.environment
    let fixtures = URL(fileURLWithPath: env["FIXTURES"]!)
    let work = FileManager.default.temporaryDirectory.appendingPathComponent("import-harness-\(UUID().uuidString)")
    let staging = work.appendingPathComponent("staging")
    try! FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
    let root = work.appendingPathComponent("root")
    let store = PendingAssessmentStore(rootDirectory: root)
    let service = VideoImportService(store: store)
    let player = KemetPlayerSummary(playerId: "PILOT_PLAYER_A", fullName: "Test Player A")

    // an older, camera-recorded record that must stay untouched
    var old = PendingAssessmentRecord(id: "OLD-1", playerId: "P9", playerName: "Old Player", localVideoRelativePath: "Recordings/OLD-1.mov", startTime: Date(), trackingMode: "smart_soccer")
    old.uploadState = .uploaded; old.serverVideoId = "VID-OLD-1"; old.stopTime = Date()
    store.upsert(old)
    try! FileManager.default.createDirectory(at: root.appendingPathComponent("Recordings"), withIntermediateDirectories: true)
    try! Data("old video".utf8).write(to: root.appendingPathComponent("Recordings/OLD-1.mov"))
    let oldBytes = try! Data(contentsOf: root.appendingPathComponent("Recordings/OLD-1.mov"))

    // ---- a landscape .mov
    let movSource = fixtures.appendingPathComponent("landscape.mov")
    let movHash = sha(movSource)
    let staged1 = copyOf(movSource, into: staging, as: "a.mov")
    let r1 = try! await service.importStagedVideo(at: staged1, player: player)
    check(!FileManager.default.fileExists(atPath: staged1.path), "the staged copy was moved, not left behind")
    check(r1.playerId == "PILOT_PLAYER_A" && r1.playerName == "Test Player A", "the selected player is on the record")
    check(r1.videoSource == "IMPORTED_VIDEO" && r1.isImportedVideo, "the record says IMPORTED_VIDEO")
    check(r1.contentSha256 == movHash, "the stored SHA-256 equals the picked file's SHA-256")
    let stored1 = store.localVideoURL(for: r1)
    check(sha(stored1) == movHash, "the durable copy is byte-identical to the picked file (not recompressed)")
    check(stored1.path.hasPrefix(root.appendingPathComponent("Recordings").path) && stored1.pathExtension == "mov", "stored under Recordings, .mov kept")
    check(r1.resolution == "320x180", "resolution \(r1.resolution ?? "nil")")
    check(abs((r1.frameRateFps ?? 0) - 30) < 0.5, "frame rate \(r1.frameRateFps ?? 0)")
    check(abs((r1.durationSeconds ?? 0) - 2) < 0.2, "duration \(r1.durationSeconds ?? 0)")
    check(r1.fileSizeBytes == Int64((try! Data(contentsOf: movSource)).count), "file size")
    check(r1.integrityFailure == nil && r1.uploadState == .localSaved, "a healthy import is LOCAL_SAVED with no integrity failure")
    check(r1.stopTime != nil, "stopTime is set so the existing upload path accepts it")
    // not saved yet: invisible to every automatic upload path
    check(r1.awaitingCoachSave && !r1.isRetryable, "an unsaved import is not retryable")
    check(!store.pendingUploads.contains(where: { $0.id == r1.id }), "an unsaved import is not in the pending uploads")
    check(store.actionablePendingCount == 0, "an unsaved import is not counted as pending")

    // ---- save, then it is an ordinary pending record
    check(service.markSaved(recordId: r1.id), "markSaved succeeds for a healthy import")
    let saved = store.record(id: r1.id)!
    check(saved.coachSavedAt != nil && !saved.awaitingCoachSave && saved.isRetryable, "after SAVE it is retryable by the existing upload code")
    check(store.pendingUploads.contains(where: { $0.id == r1.id }) && store.actionablePendingCount == 1, "after SAVE it is counted as pending")
    check(!service.discardUnsaved(recordId: r1.id), "a saved import can no longer be discarded as unsaved")
    check(FileManager.default.fileExists(atPath: stored1.path), "…and its file is still there")

    // ---- a rotated portrait .mp4: upright size, extension kept
    let mp4 = fixtures.appendingPathComponent("portrait_rotated.mp4")
    let r2 = try! await service.importStagedVideo(at: copyOf(mp4, into: staging, as: "b.mp4"), player: player)
    check(r2.resolution == "180x320", "a rotated video is measured upright: \(r2.resolution ?? "nil")")
    check(store.localVideoURL(for: r2).pathExtension == "mp4" && r2.contentSha256 == sha(mp4), "mp4 extension and hash kept")
    check(r2.integrityWarning == nil && r2.integrityFailure == nil, "a different video raises no duplicate warning")

    // ---- the same video again for the same player: a warning, not a block
    let r3 = try! await service.importStagedVideo(at: copyOf(movSource, into: staging, as: "c.mov"), player: player)
    check(r3.integrityWarning?.contains("already imported") == true && r3.integrityFailure == nil, "a repeat import warns")
    check(store.records.filter { $0.contentSha256 == movHash }.count == 2, "both copies exist under different record ids")

    // ---- cancel before save removes only the fresh copy
    check(service.discardUnsaved(recordId: r3.id), "an unsaved import can be cancelled")
    check(store.record(id: r3.id) == nil && !FileManager.default.fileExists(atPath: store.localVideoURL(for: r3).path), "cancel removes that record and its file")
    check(store.record(id: r1.id) != nil && FileManager.default.fileExists(atPath: stored1.path), "…and not the other import")
    check(!service.discardUnsaved(recordId: "OLD-1"), "an older camera record can never be discarded through import")
    check(!service.discardUnsaved(recordId: "does-not-exist"), "an unknown id is a no-op")

    // ---- problems: not a video, empty file
    let junk = try! await service.importStagedVideo(at: copyOf(fixtures.appendingPathComponent("not_a_video.mov"), into: staging, as: "d.mov"), player: player)
    check(junk.integrityFailure != nil && junk.uploadState == .failedPermanent, "a non-video is a PROBLEM and not uploadable")
    check(!junk.isRetryable && !service.markSaved(recordId: junk.id), "a problem video cannot be saved")
    let empty = try! await service.importStagedVideo(at: copyOf(fixtures.appendingPathComponent("empty.mov"), into: staging, as: "e.mov"), player: player)
    check(empty.integrityFailure != nil && !service.markSaved(recordId: empty.id), "an empty file is a PROBLEM and cannot be saved")
    check(service.discardUnsaved(recordId: junk.id) && service.discardUnsaved(recordId: empty.id), "problem imports can be cancelled away")

    // ---- it survives a restart: a new store reads the same index
    let reopened = PendingAssessmentStore(rootDirectory: root)
    let again = reopened.record(id: r1.id)
    check(again?.videoSource == "IMPORTED_VIDEO" && again?.contentSha256 == movHash && again?.coachSavedAt != nil, "the imported record survives a restart")
    check(reopened.record(id: "OLD-1") != nil, "the older record is still there")
    check(try! Data(contentsOf: root.appendingPathComponent("Recordings/OLD-1.mov")) == oldBytes, "the older video file is untouched")
    check(sha(movSource) == movHash, "the picked source file is unchanged")

    // ---- a record written before importing existed still decodes (no new keys)
    let legacy = #"[{"id":"L1","playerId":"P1","playerName":"Legacy","localVideoRelativePath":"Recordings/L1.mov","startTime":"2026-10-01T10:00:00Z","trackingMode":"smart_soccer","uploadState":"UPLOADED","uploadAttempts":1}]"#
    let legacyRoot = work.appendingPathComponent("legacy")
    try! FileManager.default.createDirectory(at: legacyRoot, withIntermediateDirectories: true)
    try! legacy.data(using: .utf8)!.write(to: legacyRoot.appendingPathComponent("pending_assessments.json"))
    let legacyStore = PendingAssessmentStore(rootDirectory: legacyRoot)
    check(legacyStore.records.count == 1 && legacyStore.quarantinedRecordCount == 0, "an old record decodes unchanged")
    check(legacyStore.records.first?.videoSource == nil && legacyStore.records.first?.awaitingCoachSave == false, "an old record is a camera recording, never awaiting a save")

    try? FileManager.default.removeItem(at: work)
    if failures.isEmpty { print("ALL CHECKS PASSED") } else { failures.forEach { print("FAIL:", $0) }; exit(1) }
}

await run()
