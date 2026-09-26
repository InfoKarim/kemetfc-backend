//
//  GimbalReconnectTests.swift
//  KemetFCTrackerTests
//
//  Bounded, cancellation-safe reconnect sequencing (DockKitManager uses it).
//  `sleep` is injected, so nothing here waits.
//

import XCTest
@testable import KemetFCTracker

private final class Recorder: @unchecked Sendable {
    private let lock = NSLock()
    private var _sleeps: [TimeInterval] = []
    private var _attempts: [Int] = []
    private var _checks = 0
    func addSleep(_ value: TimeInterval) { lock.withLock { _sleeps.append(value) } }
    func addAttempt(_ value: Int) { lock.withLock { _attempts.append(value) } }
    func addCheck() -> Int { lock.withLock { _checks += 1; return _checks } }
    var sleeps: [TimeInterval] { lock.withLock { _sleeps } }
    var attempts: [Int] { lock.withLock { _attempts } }
}

final class GimbalReconnectTests: XCTestCase {
    func testTheAccessoryNeverReturningStopsAfterExactlyTheBoundedNumberOfWindows() async {
        let recorder = Recorder()
        let outcome = await GimbalReconnect.run(
            policy: GimbalReconnectPolicy(attemptDelays: [2, 3, 5]),
            isReconnected: { false },
            onAttempt: { recorder.addAttempt($0) },
            sleep: { recorder.addSleep($0) }
        )
        XCTAssertEqual(outcome, .exhausted(attempts: 3))
        XCTAssertEqual(recorder.attempts, [1, 2, 3])
        XCTAssertEqual(recorder.sleeps, [2, 3, 5], "one growing wait per attempt — not a single delayed check")
    }

    func testReconnectingDuringASecondWindowStopsTheSequence() async {
        let recorder = Recorder()
        let outcome = await GimbalReconnect.run(
            policy: GimbalReconnectPolicy(attemptDelays: [2, 3, 5]),
            isReconnected: { recorder.sleeps.count >= 2 },          // the dock event arrives during window 2
            onAttempt: { recorder.addAttempt($0) },
            sleep: { recorder.addSleep($0) }
        )
        XCTAssertEqual(outcome, .reconnected(attempt: 2))
        XCTAssertEqual(recorder.sleeps, [2, 3], "no further windows once it is back")
    }

    func testAlreadyReconnectedDoesNotWaitAtAll() async {
        let recorder = Recorder()
        let outcome = await GimbalReconnect.run(isReconnected: { true }, sleep: { recorder.addSleep($0) })
        XCTAssertEqual(outcome, .reconnected(attempt: 1))
        XCTAssertTrue(recorder.sleeps.isEmpty)
    }

    func testCancellationDuringAWaitEndsTheSequenceImmediately() async {
        let recorder = Recorder()
        let outcome = await GimbalReconnect.run(
            isReconnected: { false },
            sleep: { seconds in recorder.addSleep(seconds); throw CancellationError() }
        )
        XCTAssertEqual(outcome, .cancelled)
        XCTAssertEqual(recorder.sleeps.count, 1, "no further windows after cancellation")
    }

    func testACancelledTaskDoesNotStartAnyWindow() async {
        let recorder = Recorder()
        let task = Task { () -> GimbalReconnectOutcome in
            await GimbalReconnect.run(isReconnected: { false }, onAttempt: { recorder.addAttempt($0) }, sleep: { _ in })
        }
        task.cancel()
        let outcome = await task.value
        XCTAssertEqual(outcome, .cancelled)
        XCTAssertTrue(recorder.attempts.isEmpty)
    }

    func testTheDefaultPolicyIsBounded() {
        let policy = GimbalReconnectPolicy()
        XCTAssertEqual(policy.maxAttempts, 3)
        XCTAssertTrue(policy.attemptDelays.allSatisfy { $0 > 0 && $0 <= 10 })
    }

    func testAnEmptyPolicyExhaustsImmediatelyInsteadOfLooping() async {
        let outcome = await GimbalReconnect.run(policy: GimbalReconnectPolicy(attemptDelays: []), isReconnected: { false }, sleep: { _ in })
        XCTAssertEqual(outcome, .exhausted(attempts: 0))
    }
}
