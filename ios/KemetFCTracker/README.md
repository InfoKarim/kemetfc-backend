# KemetFCTracker — Smart Soccer Camera Tracking (iOS source)

Native iOS companion app for KEMET FC's assessment platform: connects to
an Insta360 Flow 2 Pro over Apple DockKit, tracks the coach-selected
player and the ball, physically pans/tilts the gimbal to keep them
framed, records the assessment, and streams telemetry to the existing
KEMET backend (`app/routers/tracking.py`, in this same repository).

## Verification status — read this first

This code was written in an environment with **no full Xcode install**
(Command Line Tools only — `xcodebuild` errors out immediately, no
`simctl`) and **no physical iPhone or Insta360 Flow 2 Pro**. Per-file
status, updated for the Phase 2 pass:

| File | Status |
|---|---|
| `Sources/Tracking/TrackTypes.swift` | **Compiled AND run.** Pure Foundation. |
| `Sources/Tracking/FramingCalculator.swift` | **Compiled AND run**, 20/20 assertions passing — see `Tests/FramingMathTests.swift`. |
| `Sources/Tracking/TapToLockConverter.swift` | **Compiled AND run**, 12/12 assertions passing — see `Tests/TapToLockConverterTests.swift`. |
| `Sources/Tracking/PlayerTracker.swift` | **Compiled AND run.** A comprehensive standalone test found and led to fixing 3 real state-machine bugs (two timer bugs, one missing terminal-state guard) — final result 14/14 passing. See `Tests/PlayerTrackerTests.swift`. |
| `Sources/Vision/BallBlobDetector.swift` | **Compiled AND run**, 8/8 synthetic-image checks passing on first run — see `Tests/BallBlobDetectorTests.swift`. |
| `Sources/Vision/BallDetector.swift`, `Sources/Vision/PlayerDetector.swift` | **Type-checked** (`swiftc -typecheck`, zero errors — Vision/CoreImage/CoreVideo are available standalone on macOS, unlike DockKit/AVFoundation/SwiftUI). Fixing this file's typecheck errors for the first time caught a real bug: it previously called a non-existent API, `CIImage.cgImage(context:)` (the real API is `CIContext.createCGImage(_:from:)`) — see the file header. |
| `Sources/Tracking/BallTracker.swift` | **Type-checked** (`swiftc -typecheck`, zero errors) — pure Foundation/Combine. |
| Everything importing `DockKit`, `AVFoundation`, `Spatial`, or `SwiftUI` (`DockKitManager`, `GimbalController`, `CameraCaptureManager`, `TrackingCoordinator`, `ThermalManager`'s host call sites, all of `Sources/UI/`, `Sources/App/`, `Sources/Networking/`) | **Written, reviewed, NOT compiled.** These frameworks aren't available to `swiftc` outside a real iOS SDK/Xcode. Every DockKit/AVFoundation API signature used was individually verified against Apple's live documentation (see citations in each file's header). Careful manual review of these files during the Phase 2 pass found and fixed several real bugs that would otherwise only have surfaced in Xcode — see "Bugs found by review or execution" below. |
| Physical gimbal rotation, DockKit pairing, on-device inference performance, thermal behavior, actual video upload against a real network | **Not tested at all.** No physical hardware, no Xcode/Simulator, in this environment. |

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

Expect Xcode to find more when a real build is finally possible — this
is reviewed, unverified source code, not a finished, tested product.

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

This has **not been run in this environment** (no XcodeGen, no Xcode) —
the `.xcodeproj` this generates has never actually been opened or built.
`project.yml` declares: a `KemetFCTracker` iOS app target (deployment
target iOS 17.4, matching Insta360's own recommended DockKit minimum),
an Info.plist generated from declared properties (camera usage
description, portrait-only — matching the existing `orientation: .right`
Vision calls in `PlayerDetector.swift`), and a `KemetFCTrackerTests`
unit test target wired to `Tests/`.

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
