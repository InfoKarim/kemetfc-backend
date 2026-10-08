import Combine
import Foundation

// Pure checks of VideoFirstAssessmentModel and VideoFirstAssessmentMode, compiled with the two source files
// copied out by tests/test_ios_video_first_assessment.py. No app, camera, simulator or device is involved.

var failures: [String] = []
func check(_ condition: Bool, _ message: String) {
    if !condition { failures.append(message) }
}

typealias M = VideoFirstAssessmentModel
func facts(file: Bool = true, bad: Bool = false, phase: M.UploadPhase = .localSaved, seconds: Double? = 187.4) -> M.RecordingFacts {
    M.RecordingFacts(fileExists: file, integrityFailed: bad, uploadPhase: phase, durationSeconds: seconds)
}

// 5 and 6. A recording that failed integrity blocks SAVE; a healthy one allows it.
check(M.reviewStatus(facts()) == .ready && M.canSave(facts()), "healthy recording is READY and can be saved")
for phase in [M.UploadPhase.localSaved, .pendingUpload, .uploading, .uploaded, .failedRetryable, .failedPermanent] {
    check(M.canSave(facts(phase: phase)), "a healthy recording can be saved in upload phase \(phase)")
    check(!M.canSave(facts(bad: true, phase: phase)), "an integrity failure blocks SAVE in upload phase \(phase)")
}
if case .problem(let reason) = M.reviewStatus(facts(bad: true)) {
    check(reason.lowercased().contains("integrity") && reason.contains("Record this player again"), "the integrity problem is explained")
} else { check(false, "integrity failure is a problem") }
check(!M.canSave(facts(file: false)), "a missing file blocks SAVE")
if case .problem = M.reviewStatus(facts(file: false)) {} else { check(false, "a missing file is a problem") }
check(M.reviewStatus(facts(phase: .recording)) == .finishing && !M.canSave(facts(phase: .recording)), "a recording still being finalised cannot be saved")
check(!M.canSave(facts(file: false, bad: true)), "missing and failed together still blocks")

// Duration text never invents a number.
check(M.durationText(187.4) == "03:07", "187.4 s is 03:07")
check(M.durationText(59.9) == "00:59", "59.9 s is 00:59")
check(M.durationText(3600) == "60:00", "an hour is 60:00")
check(M.durationText(0) == "00:00", "zero is 00:00")
for odd in [nil, -1, Double.nan, Double.infinity] as [Double?] { check(M.durationText(odd) == "—", "no valid length shows a dash: \(String(describing: odd))") }

// Upload words.
check(M.uploadLabel(facts(phase: .localSaved)) == "LOCAL", "local")
check(M.uploadLabel(facts(phase: .pendingUpload)) == "PENDING" && M.uploadLabel(facts(phase: .failedRetryable)) == "PENDING", "pending")
check(M.uploadLabel(facts(phase: .uploading)) == "UPLOADING", "uploading")
check(M.uploadLabel(facts(phase: .uploaded)) == "UPLOADED", "uploaded")
check(M.uploadLabel(facts(phase: .failedPermanent)).hasPrefix("UPLOAD FAILED"), "a permanent upload failure is named, and the video is kept")
check(M.uploadLabel(facts(bad: true, phase: .failedPermanent)).hasPrefix("NOT UPLOADED"), "an invalid recording is never described as uploaded")
check(M.uploadLabel(facts(bad: true, phase: .uploaded)).hasPrefix("NOT UPLOADED"), "an integrity failure is never shown as uploaded, whatever the stored phase")

// The analysis line is honest: no pipeline is running, so it never says WAITING, PROCESSING, COMPLETE or shows a number.
check(M.analysisLabel == "NOT AVAILABLE YET", "analysis label")
for word in ["WAITING", "PROCESSING", "COMPLETE", "SCORE", "RATING"] { check(!M.analysisLabel.uppercased().contains(word), "analysis label must not say \(word)") }
check(M.analysisLabel.rangeOfCharacter(from: .decimalDigits) == nil, "no number in the analysis label")
check(M.savedNote.contains("background") && M.savedNote.contains("leave"), "the coach is told they can leave")

// 10. Saving twice never saves twice; the mode is cleared by end() and starts off.
MainActor.assumeIsolated {
    let mode = VideoFirstAssessmentMode()
    check(!mode.isActive && mode.savedRecordIds.isEmpty, "off and empty at the start")
    mode.begin(); check(mode.isActive, "begin turns it on")
    check(mode.save("REC-1") == true, "the first save counts")
    check(mode.save("REC-1") == false, "the second save of the same recording is a no-op")
    check(mode.save("REC-2") == true && mode.savedRecordIds == ["REC-1", "REC-2"], "another recording is its own save")
    mode.end(); check(!mode.isActive, "end turns it off")
    check(mode.savedRecordIds == ["REC-1", "REC-2"], "ending does not forget what was saved in this run")
}

if failures.isEmpty {
    print("ALL CHECKS PASSED")
} else {
    for failure in failures { print("FAILED: \(failure)") }
    exit(1)
}
