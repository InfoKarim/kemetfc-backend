# KemetFCTracker — Smart Soccer Camera Tracking (iOS source)

Native iOS companion app for KEMET FC's assessment platform: connects to
an Insta360 Flow 2 Pro over Apple DockKit, tracks the coach-selected
player and the ball, physically pans/tilts the gimbal to keep them
framed, records the assessment, and streams telemetry to the existing
KEMET backend (`app/routers/tracking.py`, in this same repository).

## Verification status — read this first

**Phase 3 update: this machine now has full Xcode (27.0), and the
ENTIRE app target genuinely builds** —
`xcodebuild build -project KemetFCTracker.xcodeproj -scheme
KemetFCTracker -sdk iphoneos -destination 'generic/platform=iOS'`
returns **BUILD SUCCEEDED**, and `xcodebuild build-for-testing` for the
same scheme returns **TEST BUILD SUCCEEDED** (the `KemetFCTrackerTests`
bundle, all 4 test files, compiles and links against the real device
SDK). This is a real, substantive verification tier beyond anything
Phase 1/2 could do with Command Line Tools alone — getting here required
fixing roughly 15 genuine compile/concurrency/API errors the compiler
found that no amount of `swiftc -typecheck` or manual review could have
caught (see "Phase 3: real build errors found and fixed" below).

**Still not possible in this environment: running anything.** No iOS
Simulator runtime is installed (needs 8.07GB, only 7.4GB free on this
machine) — but separately and more fundamentally, **DockKit does not
exist on the iOS Simulator SDK at all**, so this app can never build for
Simulator regardless of disk space; only a physical-device SDK build is
possible, ever. No physical iPhone or Insta360 Flow 2 Pro were connected
in this environment. So: compiled and linked, yes; launched, installed,
or run — not even once.

| File | Status |
|---|---|
| Every file in `Sources/` and `Tests/` | **Compiles and links as a real Xcode target** against the iOS 27.0 device SDK (BUILD SUCCEEDED / TEST BUILD SUCCEEDED) — see above. |
| `Sources/Tracking/FramingCalculator.swift` | Additionally **compiled AND run standalone**, 20/20 assertions passing — see `Tests/FramingMathTests.swift`. |
| `Sources/Tracking/TapToLockConverter.swift` | Additionally **compiled AND run standalone**, 12/12 assertions passing — see `Tests/TapToLockConverterTests.swift`. |
| `Sources/Tracking/PlayerTracker.swift` | Additionally **compiled AND run standalone** — found and led to fixing 3 real state-machine bugs (two timer bugs, one missing terminal-state guard); final result 14/14 passing. See `Tests/PlayerTrackerTests.swift`. |
| `Sources/Vision/BallBlobDetector.swift` | Additionally **compiled AND run standalone**, 8/8 synthetic-image checks passing on first run — see `Tests/BallBlobDetectorTests.swift`. |
| `Tests/*.swift` (all 4 files) | **Compile and link into a real `.xctest` bundle** against the device SDK. Never executed — no Simulator (architecturally impossible for this app, not just unavailable), no physical device. |
| Physical gimbal rotation, DockKit pairing, on-device inference performance, thermal behavior, actual video upload against a real network, app launch, camera preview, recording | **Not tested at all.** Nothing has ever run — this is a compile/link-time verification tier only. |

## Bugs found by review or execution (Phase 1 + Phase 2)

1. `TrackingCoordinator.computeFramingTarget()` was passing a *position*
   (`predictedCenter(dt:)`) where `FramingCalculator.applyLeadRoom`
   expects a *velocity* — fixed via `PlayerTracker.currentVelocity`.
2. Seven files using `@Published`/`ObservableObject` were missing
   `import Combine`.
3. `PlayerTracker.handleMiss()` had two related timer bugs delaying the
   TEMPORARILY_LOST → SEARCHING transition by a full interval, and
   `update()` had no guard preventing a matching detection from silently
   re-locking the tracker even while `state == .lost` — all three found
   by a real standalone-executable test run (11/14 → 14/14 after fixes).
4. `TrackingCoordinator.lockPlayer(at:...)` always locked the *nearest*
   detection with no distance cutoff — a tap on empty space far from
   everyone still silently locked whoever was closest, contradicting the
   spec's explicit "do not lock an arbitrary person" requirement. Fixed
   by wiring in `TapToLockConverter.selectDetection`, which had already
   been built and verified but never actually called from here.
5. `TrackingCoordinator.apply(result:)` built every detection's
   appearance signature as flat neutral gray via
   `AppearanceSignature(meanColor: (128,128,128))` — an API that no
   longer even exists after `PlayerTracker`'s two-region appearance
   rewrite, so this file would not have compiled in Xcode as it stood.
   Fixed with real per-region (torso/legs) pixel sampling via
   `CIAreaAverage`.
6. `AssessmentTrackingView`'s tap gesture used `.onTapGesture { location
   in ... }`, which is not a valid overload (plain `onTapGesture` takes
   no location) — and separately, the tap handler passed into it from
   `KemetFCTrackerApp.swift` was a literal no-op (`{ _ in }`). Tap-to-lock
   did not exist end to end, not merely "hardcoded to center." Fixed
   with `SpatialTapGesture` + real view/video-dimension-aware conversion
   + actual wiring through to `AssessmentSessionViewModel.handlePlayerTap`.
7. `ClassicalCVBallDetector.detectBall` called a non-existent API,
   `CIImage.cgImage(context:)` — fixed to use `CIContext.createCGImage`.

## Phase 3: real build errors found and fixed

Getting from "reviewed source" to BUILD SUCCEEDED took ~15 real, distinct
fixes across ~10 build/fix/rebuild cycles, none of which `swiftc
-typecheck` or manual review could have caught (they all depend on
DockKit/AVFoundation/full Swift 6 concurrency checking against the real
SDK):

1. `FrameForwarder.captureOutput(...)` read `captureManagerRef?.device`
   (a `@MainActor`-isolated property) from its own `nonisolated`
   delegate callback — a hard Swift 6 concurrency error. Fixed by
   capturing `device` once at `FrameForwarder.init` (on the MainActor)
   instead.
2. `cameraCaptureManager.frameDelegate = FrameForwarder(...)` assigned
   into a `weak var` with nothing else retaining the new instance —
   Xcode's own build warning was "instance will be immediately
   deallocated," meaning **the entire per-frame inference pipeline would
   silently never run in production**. Fixed with a strong
   `AssessmentSessionViewModel.frameForwarder` property.
3. `TrackingCoordinator.runInference(...)` (a `nonisolated` function on
   a `@MainActor` class) read `playerDetector`/`ballDetector` — real
   Swift 6 concurrency errors, since neither type is `Sendable`. Fixed
   with `nonisolated(unsafe)`, documented against the real invariant
   that makes it safe (single-flight via `isInferring`).
4. `DockKitManager.handle(stateChange:)` treated `stateChange.accessory`
   as non-optional — re-verified against Apple's live docs:
   `DockAccessory.StateChange.accessory` is genuinely `DockAccessory?`.
   Exactly the kind of silent wrong assumption this whole verification
   pass exists to catch.
5. `BallBlobDetector.Parameters` needed explicit `Sendable` conformance
   for its `static let default` to be usable across actor boundaries.
6. `TelemetryUploader.pendingSamples` had **zero synchronization**
   despite being mutated from both `@MainActor` call sites and its own
   background flush `Task` — a genuine pre-existing data race, not a
   false positive. Fixed with a real `NSLock` (`withLock`, never held
   across `await`) and `@unchecked Sendable`.
7. `ThermalManager`'s `deinit` read a non-Sendable `NSObjectProtocol?`
   observer token — `nonisolated(unsafe)`, safe because
   `NotificationCenter.removeObserver` is documented thread-safe.
8. `TrackingCoordinator.processFrame`'s `Task.detached` sent
   `CVPixelBuffer`/`AVCaptureDevice` (both non-Sendable) across an
   isolation boundary — fixed with an explicit `UncheckedSendableBox`
   documenting the real single-ownership handoff invariant, not a
   blanket suppression. The same pattern was needed again for
   `FrameForwarder`'s `CMSampleBuffer`/`AVCaptureDevice`.
9. `KemetAPIClient.csrfToken` was an unsynchronized `var` on a class
   called from multiple contexts — fixed with a real `NSLock`.
10. `TelemetryUploader.recordEvent`'s `details: [String: Any]` was
    non-Sendable by construction (`Any` can hold anything). Replaced
    with a closed, genuinely-`Sendable` `TelemetryEventValue` enum —
    this is a real correctness improvement, not just a compiler
    workaround, since the old `AnyEncodable` silently encoded unknown
    types as `null` instead of failing.
11. `GimbalController.pauseMotorCorrection()` called
    `accessory.setAngularVelocity(_:)` — documented in this same file's
    own header as `async throws` — as if it were synchronous. A real
    Xcode build rejected this outright ("'async' call in a function
    that does not support concurrency"). Fixed by wrapping in a `Task`,
    matching the pattern `updateTarget(...)` already used correctly.
12. `DockKitManager.observeBattery(for:)` treated each element of
    `accessory.batteryStates` as a **collection** needing `.first` —
    re-verified against Apple's docs: `DockAccessory.BatteryStates
    .Element` is directly `DockAccessory.BatteryState`, one value per
    iteration. The property access also genuinely throws on this SDK
    (a real Xcode build rejected the missing `try`). Also added a
    missing `@available(iOS 18.0, *)` guard — `batteryStates` needs
    iOS 18.0, but the deployment target is 17.4 (Insta360's own
    recommended DockKit minimum); this mismatch had never been
    enforced before a real build existed to enforce it.
13. `TelemetryUploader.flush()`'s manual `lock.lock()`/`unlock()` pairs
    are flatly rejected by Swift 6 **inside `async` functions**
    ("unavailable from asynchronous contexts") — fixed with the
    closure-based `NSLock.withLock` API Apple added for exactly this.
14. A local-network App Transport Security gap: this README's own
    documented on-field workflow (`KEMET_BACKEND_BASE_URL =
    http://<mac-LAN-IP>:8010`) would be silently blocked by ATS on a
    real device with no exception configured. Fixed by adding
    `NSAppTransportSecurity.NSAllowsLocalNetworking` (scoped to literal
    IPs/`.local` hosts only — production HTTPS traffic is unaffected)
    to `project.yml`.

Expect Xcode to find more once this actually runs on a device — a
successful *build* proves the code is well-typed and concurrency-safe
by the compiler's own strict rules; it proves nothing about runtime
behavior, UI correctness, or whether the DockKit/AVFoundation calls
actually do the right thing against real hardware.

## Why DockKit/AVFoundation API details in the code comments are
## unusually specific

Every `DockKit`/`Vision`/`AVFoundation` type and method signature
referenced in this module's comments was looked up live against
`developer.apple.com/documentation` (fetched via its JSON API) during
this session — not recalled from training data alone, because several
recalled signatures were checked against reality and found wrong (e.g.
`DockAccessory.Limits.Limit` really takes `positionRange:maximumSpeed:`,
not `min:max:`; `recognizedPoints(_:)` takes its argument positionally,
not as `forGroupKey:`; `URLSession.upload(for:fromFile:delegate:)` and
`AVAsynchronousKeyValueLoading.load(_:)` were both re-verified before
use in `VideoUploadManager.swift`/`AssessmentSessionViewModel.swift`).
This is why file headers cite exact iOS version numbers per API.

## Setting up the real Xcode project

`project.yml` (XcodeGen spec) now exists at the root of this directory —
this repo's answer to "make the iOS code a real openable Xcode project"
without hand-maintaining a binary `.xcodeproj`. On a Mac with Xcode and
[XcodeGen](https://github.com/yonaskolb/XcodeGen) installed:

```bash
brew install xcodegen   # if not already installed
cd ios/KemetFCTracker
xcodegen generate
open KemetFCTracker.xcodeproj
```

**This has now been run** (Phase 3: XcodeGen 2.46.0 installed via
Homebrew, full Xcode 27.0 available) — the generated `.xcodeproj`
genuinely builds (see "Verification status" above). The generated
`.xcodeproj` and `Sources/Info.plist` are gitignored (regenerable build
output, not source — `project.yml` is the source of truth). `project.yml`
declares: a `KemetFCTracker` iOS app target (deployment target iOS 17.4,
matching Insta360's own recommended DockKit minimum), an Info.plist
generated from declared properties (camera usage description,
portrait-only — matching the existing `orientation: .right` Vision calls
in `PlayerDetector.swift` — plus a scoped local-networking ATS exception
for on-field LAN testing), and a `KemetFCTrackerTests` unit test target
wired to `Tests/`.

### Manual Xcode project (fallback if XcodeGen isn't available)

1. Xcode → File → New → Project → iOS → App. Product name
   `KemetFCTracker`, interface **SwiftUI**, language **Swift**.
   Deployment target **iOS 17.4** (18.0 if you want
   `DockAccessory.TrackedPerson`/`TrackingStates`, referenced only in
   comments/future work here).
2. Delete the template's generated `ContentView.swift`/`...App.swift`.
3. Drag the `Sources/` folder into the Xcode project navigator ("Copy
   items if needed", add to the `KemetFCTracker` target).
4. Add frameworks under *Frameworks, Libraries, and Embedded Content*:
   DockKit, AVFoundation, Vision, CoreImage, Spatial. (Combine and
   Foundation are implicit.)
5. Add the `NSCameraUsageDescription` Info.plist key (required — this
   app is camera-first).
6. Create a `KemetFCTrackerTests` unit test target (iOS Unit Testing
   Bundle), drag every file under `Tests/` into it.
7. Set `KEMET_BACKEND_BASE_URL` in the scheme's Run action environment
   variables — `https://app.kemetfc.com` for production, or
   `http://<your-mac-LAN-IP>:8010` for on-field testing against a
   laptop running `uvicorn main:app` from this repo.
8. Build (⌘B). Fix whatever Xcode's real compiler finds.

## Known gaps still open after Phase 3

- **This app has never been run, launched, or installed anywhere.**
  Compiling and linking successfully (Phase 3) is a real but limited
  verification tier. Nothing about app launch, camera preview,
  DockKit pairing, gimbal motion, recording, or upload has been
  observed. See `VERIFICATION_CHECKLIST.md` for the exact remaining
  Stage A–E procedure on real hardware.
- **iOS Simulator can never run this app.** Not a temporary/disk-space
  limitation — `import DockKit` fails to resolve on the
  `iphonesimulator` SDK entirely (confirmed by attempting a Simulator
  build). Every future verification step needs a physical iPhone.

## Known gaps still open after Phase 2

- **Ball detection has no trained model — by design, not oversight.**
  `ClassicalCVBallDetector` now runs a real (if weak) classical-CV
  algorithm (`Sources/Vision/BallBlobDetector.swift`, 8/8 synthetic
  tests passing) — a genuine connected-component blob search, not a
  stub — but it is explicitly NOT a trained model, and
  `TrackingSessionDB.ball_model_status` is reported as
  `"fallback_classical"` (never `"installed"`) so the backend/UI can
  honestly show this. `AssessmentTrackingView` now surfaces a permanent,
  non-dismissible badge reflecting this exact status.
- **No native login view.** `AssessmentSessionViewModel.coachIdentifier`
  is a hardcoded placeholder (`"ios-app-unidentified-coach"`) used for
  video-upload metadata's `created_by` field — `KemetAPIClient` still
  assumes a session cookie already exists (e.g. from a WKWebView-hosted
  `/login`), and no coach-credential-entry screen exists in this module.
- **QR-scan player selection is described in a comment, not
  implemented** (`PlayerSelectionView.swift`) — out of the Phase 2
  instructed pipeline order, not attempted this pass.
- **`Limits.yaw` range `-π..<π`** assumes the Flow 2 Pro's claimed 360°
  pan; confirm against real hardware and Apple's DockKit sample rather
  than trusting this comment's assumption.
- **The multipart video upload (`VideoUploadManager.swift`) has never
  actually sent a byte over a real network** — it is written against
  the real backend endpoint's real schema and re-verified Apple APIs,
  but its retry/backoff/progress/cancellation logic has only been
  reviewed, not exercised.

## What's already built and tested on the backend side

`app/routers/tracking.py`, `app/services/tracking_service.py`,
`tracking_features.py`, `tracking_quality.py` (now with ID-switch-risk/
target-lost-duration/telemetry-completeness gating), `skill_inference.py`,
`heatmap_service.py`, `model_registry_service.py`, and the Phase 2
model-version-traceability columns on `TrackingSessionDB` are real,
running, and covered by **921 passing backend tests** (1 skipped)
against both SQLite and a real disposable PostgreSQL container, with
`ruff check .` clean. This iOS app remains the only unverified layer —
the server it talks to is not.
