import XCTest
@testable import KemetFCTracker

final class RecordingIntegrityTests: XCTestCase {
    private func media(_ duration: Double, size: Int64 = 500_000, video: Bool = true, exists: Bool = true) -> RecordingMeasurement {
        RecordingMeasurement(fileExists: exists, sizeBytes: size, hasVideoTrack: video, durationSeconds: duration)
    }

    /// The exact error a physical iPhone reported on every failed recording.
    private let stopped = RecordingFinalizationError(
        domain: "AVFoundationErrorDomain", code: -11818,
        underlyingDomain: "NSOSStatusErrorDomain", underlyingCode: -16414,
        reportedSuccessfullyFinished: true, message: "Recording Stopped"
    )

    private func isInvalid(_ v: RecordingIntegrity.Verdict) -> Bool {
        if case .invalid = v { return true }
        return false
    }

    // MARK: - The confirmed field failure

    func testTheRealZeroPointOneFourEightSecondFileFailsFor30SecondAssessment() {
        let v = RecordingIntegrity.evaluate(measurement: media(0.148, size: 514_134), wallSeconds: 30, finalizationError: stopped)
        guard case .invalid(let reason) = v else { return XCTFail("a 0.148 s file for a 30 s assessment must be invalid, got \(v)") }
        XCTAssertTrue(reason.contains("0.1"))
        XCTAssertTrue(reason.contains("-11818"), "the camera's own error must be surfaced: \(reason)")
    }

    func testShortFileFailsEvenWhenAVFoundationReportedNoError() {
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(0.4), wallSeconds: 20, finalizationError: nil)))
    }

    // MARK: - Coverage tolerance

    func testFullLengthFileIsValid() {
        XCTAssertEqual(RecordingIntegrity.evaluate(measurement: media(29.8), wallSeconds: 30, finalizationError: nil), .valid(warning: nil))
    }

    func testCoverageBoundaryIsWallTimesEightyPercentMinusOneSecond() {
        // 30 s -> required 23.0 s
        XCTAssertEqual(RecordingIntegrity.requiredDuration(wallSeconds: 30), 23.0, accuracy: 1e-9)
        XCTAssertEqual(RecordingIntegrity.evaluate(measurement: media(23.0), wallSeconds: 30, finalizationError: nil), .valid(warning: nil))
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(22.9), wallSeconds: 30, finalizationError: nil)))
    }

    func testVeryShortTapAndReleaseIsNotJudgedByCoverage() {
        XCTAssertEqual(RecordingIntegrity.requiredDuration(wallSeconds: 1.5), 0)
        XCTAssertEqual(RecordingIntegrity.evaluate(measurement: media(0.2), wallSeconds: 1.5, finalizationError: nil), .valid(warning: nil))
    }

    // MARK: - Structural failures

    func testMissingEmptyAndVideolessFilesAreInvalid() {
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(0, size: 0, video: false, exists: false), wallSeconds: 30, finalizationError: nil)))
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(30, size: 0), wallSeconds: 30, finalizationError: nil)))
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(30, video: false), wallSeconds: 30, finalizationError: nil)))
    }

    func testNonFiniteOrZeroDurationIsInvalid() {
        for d in [0.0, -1.0, Double.nan, Double.infinity] {
            XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(d), wallSeconds: 1, finalizationError: nil)), "duration \(d)")
        }
    }

    // MARK: - Finalization errors are never silently dropped

    func testGoodFileWithReportedSuccessfulFinishKeepsTheErrorAsWarning() {
        let v = RecordingIntegrity.evaluate(measurement: media(29.5), wallSeconds: 30, finalizationError: stopped)
        guard case .valid(let warning) = v else { return XCTFail("\(v)") }
        XCTAssertNotNil(warning)
        XCTAssertTrue(warning?.contains("-11818") == true)
    }

    func testGoodLookingFileWhereAVFoundationDidNotFinishSuccessfullyIsInvalid() {
        var failed = stopped
        failed.reportedSuccessfullyFinished = false
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(29.5), wallSeconds: 30, finalizationError: failed)))
        failed.reportedSuccessfullyFinished = nil
        XCTAssertTrue(isInvalid(RecordingIntegrity.evaluate(measurement: media(29.5), wallSeconds: 30, finalizationError: failed)))
    }

    // MARK: - Error capture

    func testFinalizationErrorCapturesCodeUnderlyingCodeAndFinishedFlag() {
        let underlying = NSError(domain: "NSOSStatusErrorDomain", code: -16414)
        let error = NSError(domain: "AVFoundationErrorDomain", code: -11818, userInfo: [
            NSLocalizedDescriptionKey: "Recording Stopped",
            NSUnderlyingErrorKey: underlying,
            "AVErrorRecordingSuccessfullyFinishedKey": true
        ])
        let captured = RecordingFinalizationError(error)
        XCTAssertEqual(captured, stopped)
        XCTAssertEqual(captured.summary, "AVFoundationErrorDomain -11818 (underlying NSOSStatusErrorDomain -16414)")
    }

    func testFinalizationErrorSurvivesEncodeDecode() throws {
        let data = try JSONEncoder().encode(stopped)
        XCTAssertEqual(try JSONDecoder().decode(RecordingFinalizationError.self, from: data), stopped)
    }
}
