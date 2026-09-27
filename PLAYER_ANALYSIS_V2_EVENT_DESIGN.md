# Player Analysis V2 — Temporal Event Detection Engine (Design Only)

Baseline: commit `791db44` ("Add trustworthy Player Live Analysis foundation"). This
document proposes design only — no production code, no tests, no commit.

```
Existing CV / Tracking
        ↓
PlayerAnalysisEngine (V1 measurements)
        ↓
Temporal Event Detector (V2)          <- this document
        ↓
Low-level measurable events
        ↓
Future Football Event Classifier      <- design sketch only, Phase 12
        ↓
Future Tactical Interpretation
```

---

## 1. Available V1 temporal inputs (audit of commit 791db44)

| Signal | Source | Units | Update rate | Missing-data behavior | Confidence | Suitable for V2 today? |
|---|---|---|---|---|---|---|
| `timestamp` | `TrackingCoordinator.processFrame`'s `sessionElapsedSeconds`, itself `FrameForwarder`'s `CMSampleBufferGetPresentationTimeStamp` elapsed since `sessionStartTime` (`Sources/App/AssessmentSessionViewModel.swift:1040-1042`) | seconds, **real video presentation time**, not `Date()` | once per processed inference tick (≤15Hz) | always present when `observe()` is called | n/a | **YES**, but see §13's timestamp-origin finding |
| `rawSpeed` (magnitude only) | `PlayerAnalysisEngine.observe()`, computed from `PlayerTracker.currentVelocity` | normalized image-space units/second | per tick, only when player observed | `nil` when not observed this frame | none separate from tracking confidence | **YES for magnitude**; **NO for direction — the vector's angle is computed then discarded** (see §3) |
| `movementState` | `PlayerAnalysisEngine` (V1), hysteresis-smoothed | enum | per tick | `.uncertain` when not observed | derived, not a probability | **YES** — V2 should consume this rather than re-threshold `rawSpeed` |
| Player center | Derivable from `PlayerTracker.currentBoundingBox.center` | normalized (0...1) | per committed frame only | box is nil on any miss (temporarily_lost/searching/lost) | tied to tracking confidence | **YES, derivable**, but **not currently retained in V1's history/snapshot at all** — V2 needs it added |
| Player velocity **vector** (not just magnitude) | `PlayerTracker.currentVelocity` (`Sources/Tracking/PlayerTracker.swift:107`) | normalized units/sec, `NormalizedPoint(x,y)` | updated only in `commit()`; **holds its LAST value during a miss, is not reset to zero** — V1's engine only reads it when `playerObserved` is true, correctly avoiding the stale-value trap | angle information exists here but is discarded by V1's `rawSpeed` computation | tied to tracking confidence | **Exists upstream, NOT exposed by V1** — see §3, "REQUIRES V1 DATA-CONTRACT EXTENSION" |
| Player bounding-box dimensions | `PlayerTracker.currentBoundingBox.width/height` | normalized | per committed frame | nil on miss | n/a | **YES, derivable**, not currently retained |
| `trackingState` | `PlayerAnalysisEngine` (mirrors `PlayerTrackState`) | enum | per tick | `.unavailable` before any lock | n/a | **YES** |
| `trackingConfidence` | `PlayerTracker.currentConfidence`, passed through V1 | 0...1 | per tick | `nil` when not observed | real | **YES** |
| `ballVisible` | V1, from `BallTracker.currentBoundingBox != nil` | bool | per tick | always defined | n/a | **YES** |
| `ballConfidence` | `BallTracker.currentConfidence` (ultimately `ClassicalCVBallDetector`, floor ~0.20) | 0...1 | per tick | `nil` when not visible | real, deliberately low ceiling | **YES** |
| Ball center | Derivable from `BallTracker.currentBoundingBox.center` | normalized | per tick when detected | nil when not detected | tied to ball confidence | **YES, derivable**, not currently retained |
| `normalizedPlayerBallDistance` | V1 (`PlayerAnalysisEngine.observe()`), body-widths | scalar | per tick, only when both observed above the proximity-confidence floor | `nil` otherwise | inherits the weaker of the two confidences (not combined numerically) | **YES** — already exactly what §4/§5 need, present in `PlayerAnalysisHistoryEntry`, but **NOT in the live `PlayerLiveAnalysisSnapshot`** (only the classified `near/away/unavailable` state is public there) |
| Ball velocity | `BallTracker.velocity` (`Sources/Tracking/BallTracker.swift:62`) — computed internally for prediction | normalized units/sec | per tick when tracked | n/a | n/a | **Computed internally, but `private` — not exposed anywhere.** Requires a new, small, well-justified public getter (not new CV — the value already exists) — see §6 |
| Ball track ID | `BallTracker.currentTrackId` | Int? | per tick | nil when not detected | n/a | Available but not obviously needed for V2.0's proposed events |

**Key finding carried into every later section:** V1's public outputs (`PlayerLiveAnalysisSnapshot` for live use, `PlayerAnalysisHistoryEntry` for bounded retrospective use) are sufficient for **movement-state and ball-proximity-state** temporal events as-is, but **direction-change and true ball-co-movement events need V1's data contract extended** (retain velocity angle; expose ball velocity) — these are flagged as "REQUIRES NEW DERIVED SIGNAL," never as "requires new CV," since nothing here needs a new detector/model, only retaining/exposing values the CV layer already computes.

---

## 2. Proposed event schema

```swift
public struct PlayerAnalysisEvent: Equatable, Sendable {
    public let id: UUID
    public let type: PlayerAnalysisEventType
    public let startTimestamp: TimeInterval   // same sessionElapsedSeconds clock as V1
    public let endTimestamp: TimeInterval?    // nil for an instantaneous/point event
    public var duration: TimeInterval? { endTimestamp.map { $0 - startTimestamp } }
    public let evidenceQuality: EventEvidenceQuality   // .high/.medium/.low — see §8, never a fabricated number
    public let supportingMeasurements: PlayerAnalysisEventEvidence  // the raw numbers that justified it (§8)
}
```
`PlayerAnalysisEventType` is a plain `enum` (movementStarted, movementStopped, ...) per §7/12 — a data/measurement label, never a tactical judgment (no "goodMovement", no "possessionGained"). `supportingMeasurements` is a small struct carrying exactly the numbers used by the rule that fired (e.g. distance-at-start, distance-at-end, sample count) — this is what lets a coach/engineer later audit *why* an event fired, and is what any future football-event classifier (§12) would consume as its own input, never re-deriving from raw video.

---

## 3. Movement event definitions

V2 consumes V1's already-hysteresis-smoothed `movementState`, never re-thresholds `rawSpeed` itself (per your instruction — V1 owns movement classification).

- **`movementStarted`**: `movementState` transitions `.stationary` (or `.uncertain`) → `.moving` **and remains `.moving` for a minimum hold time** (proposed: 0.5s, i.e. ~7-8 samples at the ≤15Hz cap) before the event is finalized — this is a debounce ON TOP of V1's own 3-frame hysteresis, because a single V1-confirmed "moving" tick could still be a brief detector blip; V2's event layer trades a little latency for near-zero false starts.
- **`movementStopped`**: mirror — `.moving` → `.stationary`, held for the same minimum time.
- **`sustainedMovement`**: `.moving` maintained continuously for a longer minimum duration (proposed: 3s) — a single event marking "this was a real sustained run," not a repeat of `movementStarted`.
- Any transition through `.uncertain` (tracking lost) **resets the in-progress candidate** for all three — never bridges an event across a tracking gap by assuming continuity.

**Direction change — mathematically specified, not implemented until validated:**
- **Minimum speed before direction is meaningful**: `rawSpeed ≥ 2× movementSpeedThreshold` (i.e. ≥0.08 units/sec) — direction of near-zero motion is meaningless noise.
- **Minimum angular change**: ≥45° (`π/4` rad) between a "before" window's mean velocity angle and an "after" window's mean velocity angle — a much larger bar than the torso-orientation experiment's 20°, because this is claiming actual path change, not a proxy signal.
- **Temporal window**: two adjacent windows of ≥3 qualifying (speed-eligible) samples each, not single-frame vectors — angle is computed from the WINDOW's mean velocity vector, not frame-to-frame, to reject single-sample noise.
- **Persistence requirement**: the new heading must hold for the window's full duration, not just the boundary sample.
- **Noise rejection**: angle wraparound handled correctly (same fix as `TorsoOrientationExperiment.isOrientationChange`); a candidate direction change is discarded if `trackingConfidence` dips below a floor (proposed 0.5) anywhere in either window.
- **Tracking-confidence requirement**: both windows must be fully `playerObserved` (V1's `trackingState == .locked`) — a window straddling a temporarily-lost gap never qualifies.
- **This requires the V1 data-contract extension from §1** (retaining velocity angle, not just `rawSpeed` magnitude) before it can be implemented at all — classified **IMPLEMENT WITH NEW DERIVED SIGNAL**, not V2.0-ready as V1 stands today.

---

## 4. Ball approach/separation definition

Never one-frame distance — always a trend over a window of `normalizedPlayerBallDistance` samples (already computed by V1, already body-width-normalized).

- **Eligibility per sample**: `ballConfidence ≥ minimumBallConfidenceForProximityAnalysis` (V1's existing 0.20 floor) AND `trackingState == .locked` — reuses V1's own eligibility rule for the underlying distance value, never a separate one.
- **`ballApproaching`**: over a window of N consecutive ELIGIBLE samples (proposed N=5), the distance is monotonically non-increasing within a noise tolerance (proposed: each step's increase, if any, is ≤10% of the window's total decrease) AND the total decrease over the window exceeds a minimum meaningful delta (proposed: ≥0.3 body-widths) — both the "consistently decreases" and "minimum delta" requirements from your spec.
- **`ballSeparating`**: mirror, only evaluated once a `near` relationship has existed (per §5) — separation is meaningful relative to having been close, not just "distance went up from very far."
- **Consecutive-measurement requirement**: a single gap (ball not eligible for one sample) breaks the window — the next window starts fresh, never bridges a dropout by interpolating.
- **Minimum meaningful distance delta**: prevents reporting "approaching" from sub-noise wobble around a stable distance.

---

## 5. Near-zone event state machine

A small machine over V1's existing `ballProximityState` (near/away/unavailable), NOT a reimplementation of proximity classification:

```
                 (near, held >= minSustainDuration)
   ┌─────────┐  near→away  ┌──────┐  away→near  ┌───────────────┐
   │  AWAY   │◄────────────│ NEAR │────────────►│ NEAR_SUSTAINED│
   └────┬────┘             └──┬───┘             └───────┬───────┘
        │  unavailable(any)   │  unavailable(any)        │ unavailable/away
        ▼                     ▼                          ▼
                    ┌─────────────────┐
                    │   UNAVAILABLE   │  (no event fires from/into this state;
                    └─────────────────┘   it only interrupts an in-progress one)
```
- **`ballEnteredNearZone`**: `AWAY`/`UNKNOWN` → `NEAR` transition (mirrors V1's own Schmitt-trigger transition into `.near` — V2 does not re-threshold, it listens for the transition).
- **`ballStayedNear`**: fires once when `NEAR` has held continuously for a minimum duration (proposed 1.0s) — the `NEAR_SUSTAINED` state; does not repeat while sustained (debounced, §10).
- **`ballLeftNearZone`**: `NEAR` or `NEAR_SUSTAINED` → `AWAY`.
- **`UNAVAILABLE` is explicitly a non-event interruption**, not a state event of its own in V2.0 (a separate `ballBecameUnavailable`/`ballBecameVisible` PAIR is proposed in §7's TRACKING category instead, kept distinct from the near/away geometry machine).
- Never labeled touch/possession/receive/dribble — purely geometric-temporal, exactly as specified.

---

## 6. Co-movement feasibility ("ball moved with player")

**Current reliable ball velocity? NO** — `BallTracker.velocity` (`BallTracker.swift:62`) is `private`, used only for the tracker's own next-frame prediction gate; nothing outside the class can read it today.

Two paths, both **NOT implemented in V2.0**:

- **A — Expose the existing signal** (`IMPLEMENT WITH NEW DERIVED SIGNAL`): add a `public private(set) var currentVelocity: NormalizedPoint` to `BallTracker`, mirroring `PlayerTracker`'s own naming/shape exactly — this is not new CV, the value is already computed every tick for the tracker's own internal use; it only needs a public getter. This is the *only* way to get a real ball-direction signal for a genuine co-movement comparison (ball vector similar direction to player vector).
- **B — Distance-only proxy** (`IMPLEMENT NOW`, weaker evidence, explicitly labeled as such): define `ballMovedWithPlayer` using only existing V1 signals — `movementState == .moving` AND `ballProximityState ∈ {near, near_sustained}` AND the distance stays within a narrow band (not approaching or separating per §4) for a minimum duration. This is real, honest, buildable today, but it is co-presence + stable-distance, not proven co-movement (a stationary ball a player is merely standing near while shuffling in place could satisfy it) — must be named accordingly if shipped (e.g. `ballAccompanyingPlayer`, not `ballMovedWithPlayer`) to avoid overclaiming.

**Recommendation: defer real `ballMovedWithPlayer` to whenever path A is built (V2.1)**; do not ship path B under the stronger name.

---

## 7. Camera/gimbal-motion risk matrix

The camera actively pans/tilts (`GimbalController`/`FramingPipeline`) to keep the player framed — image-space motion is PLAYER MOTION + CAMERA MOTION, uncompensated (capability audit §7, unchanged by this design).

| Proposed event | Camera-motion risk | Why |
|---|---|---|
| `movementStarted`/`movementStopped`/`sustainedMovement` | **C — strongly invalidated** | The gimbal actively corrects toward the player; a well-tracked player can show near-zero image velocity while physically sprinting (successful tracking), or nonzero velocity while physically still (gimbal correcting/overshooting). `rawSpeed` conflates both. |
| Direction change | **C — strongly invalidated** | Same mechanism, worse: a gimbal correction reversing direction can look identical to the player reversing direction. This is the single riskiest proposed event and should ship with the most conservative thresholds and be treated as provisional even after implementation. |
| `ballApproaching`/`ballSeparating` (relative distance) | **A — reasonably camera-motion invariant** | Both player and ball experience *similar* camera-induced image-space displacement (same pan/tilt affects the whole frame roughly uniformly at typical assessment framing distance) — the RELATIVE distance between them is far less contaminated than either object's absolute velocity. This is exactly why §4 is built on distance-over-time, not on two independent velocities. |
| Near-zone transitions (§5) | **A — reasonably camera-motion invariant** | Same reasoning — geometric relationship, not absolute motion. |
| Co-movement (§6, path A) | **B — partially affected** | Comparing two velocity VECTORS (player, ball) is more robust than either alone (common camera-motion component partially cancels when comparing directions), but not immune — a hard gimbal correction could momentarily align both objects' apparent motion by coincidence. Must be treated as medium-confidence evidence only. |

**Can existing gimbal telemetry help compensate?** `GimbalController` does not currently expose the accessory's own orientation/motion telemetry to `TrackingCoordinator` at all (confirmed: no such property is read anywhere in `TrackingCoordinator.apply(result:)`). DockKit's `DockAccessory` API surface used elsewhere in this codebase (`DockKitManager.swift`) is oriented around connection/battery/state, not continuous orientation streaming for compensation. Real camera-motion compensation would need a new, real DockKit integration (reading the accessory's actual pan/tilt rate) — this is a **REQUIRES NEW CV/ENGINEERING** item, out of scope for V2.0/V2.1, and should be tracked as a distinct future milestone before any absolute-motion event (movement/direction) is trusted as more than a provisional signal.

---

## 8. Event confidence / evidence policy

**No generic combined "AI confidence."** Per-event, `evidenceQuality` is a plain `.high`/`.medium`/`.low` enum derived from explicit, documented rules — never a weighted formula unless one is separately justified:

- **`.high`**: all contributing samples had `trackingConfidence ≥ 0.7` (and `ballConfidence` above the proximity floor, for ball-relationship events), the full required window/duration was met with zero gaps, and (for direction change) both windows' angular spread was tight.
- **`.medium`**: the rule's minimums were met, but confidence dipped between 0.5-0.7 at some point, or one short gap was tolerated within a window.
- **`.low`**: the rule barely cleared its minimums (e.g. distance delta just above the noise floor, duration just at the minimum).
- `supportingMeasurements` on every event retains the actual numbers (start/end distance, sample count, min/max confidence seen) so a human or a later classifier can re-judge quality with full information rather than trusting only the label.

This is the same philosophy as V1's "state, not score" decision — extended to events without inventing a new numeric confidence with no defensible meaning.

---

## 9. Event state machines — missing/edge-case behavior

Applies to every proposed machine in §3/§5:

- **Missing ball**: any sample with `ballVisible == false` is treated identically to `.unavailable` proximity — interrupts (never completes) an in-progress approach/separation window; does not itself end an already-open `NEAR_SUSTAINED` event faster than a real `near→away` would (a single missing-ball tick is NOT the same as a confirmed away transition — V1's own hysteresis already governs that boundary; V2 does not add a second, conflicting timeout).
- **Low-confidence ball**: identical treatment to "not eligible" (§4) — never silently downgraded to a lower-confidence event; the sample is simply excluded from window math.
- **Temporarily lost player**: immediately voids any in-progress movement/direction candidate (§3) and any in-progress ball-relationship window that requires `trackingState == .locked`; does not retroactively delete an ALREADY-COMPLETED event.
- **Reacquisition**: a fresh start for all in-progress candidates — V2 never assumes continuity of a movement/direction candidate across a loss, even if the reacquired trajectory looks consistent (that judgment belongs to `PlayerTracker` itself, already handled there — V2 must not re-litigate it).
- **Assessment ending mid-event**: an in-progress (not-yet-finalized) candidate is simply discarded, never force-completed with a synthetic end timestamp. An already-finalized event with no natural "end" (e.g. `ballEnteredNearZone`, a point event) is unaffected.
- **Short detector dropout**: bridged only where already defined above (a single missing/low-confidence ball sample does not itself break a `NEAR_SUSTAINED` state, since that state is governed by V1's own hysteresis) — but DOES break an in-progress approach/separation WINDOW per §4 (a stricter, intentionally more conservative rule for trend-based events than for simple state persistence).

---

## 10. Event debounce / cooldown rules

| Event | Minimum duration to fire | Debounce/cooldown |
|---|---|---|
| `movementStarted`/`movementStopped` | 0.5s hold after V1's own transition | Cannot re-fire the same type again until the opposite type has fired (a real state machine per player-tracker-style enum, not a raw timer) — eliminates the exact "started/stopped/started/stopped" spam scenario named in your instructions. |
| `sustainedMovement` | 3.0s continuous `.moving` | Fires once per continuous moving interval; a `movementStopped` (even brief) resets eligibility for a new `sustainedMovement`. |
| Direction change | Two full qualifying windows (~1-2s total depending on sample rate) | Minimum 1.0s cooldown after firing before a new direction-change candidate can begin — prevents rapid re-triggering from a genuinely swerving run being chopped into many tiny "changes." |
| `ballApproaching`/`ballSeparating` | Full window (§4) | A new approach cannot begin until the previous one's window has fully closed (either by continuing, by reversing into separation, or by becoming ineligible) — no overlapping windows. |
| `ballEnteredNearZone`/`ballLeftNearZone` | Instant on V1's own (already-hysteresis-smoothed) transition | Inherently debounced by V1's Schmitt trigger already; V2 adds no further delay to these two point events. |
| `ballStayedNear` | 1.0s sustained | Fires once per continuous near interval, not repeatedly while still sustained. |

All "reset conditions" above are explicit state-machine transitions (§9), never a bare timer that could leave an event half-open indefinitely.

---

## 11. Session event timeline design

Bounded by construction: events are rare relative to raw samples (a 60-second assessment might produce tens of events, not thousands of samples) — a plain, unbounded-in-practice `[PlayerAnalysisEvent]` array is acceptable, but for defense-in-depth apply the SAME kind of cap V1 uses for `history` (e.g. 2,000 events — orders of magnitude more than any real assessment could produce, purely a safety bound, never expected to bind in practice). `PlayerAnalysisEngine.reset()` (already called on every player [re]lock, per the V1 pre-commit fix) must also clear the event timeline — same session-isolation guarantee extended to events.

---

## 12. Video timestamp strategy (important — a real finding, not an assumption)

**Wall-clock `Date()` and the video's own internal timeline are NOT the same clock, and there is a measurable, non-zero offset between them.**

- Every `PlayerAnalysisEvent.startTimestamp` uses `sessionElapsedSeconds` — which is `FrameForwarder`'s own elapsed-since-`sessionStartTime` clock, itself derived from real `CMSampleBufferGetPresentationTimeStamp` values (`AssessmentSessionViewModel.swift:1040-1042`), **not** `Date()`. This part is good: it is a real video-relative clock, suitable in principle for later seeking into the recorded file.
- **However**, `sessionStartTime` is set to the presentation timestamp of the FIRST frame `FrameForwarder` ever receives — and `FrameForwarder` is only assigned as `cameraCaptureManager.frameDelegate` inside `wireTracking()`, which runs AFTER `didStartRecordingTo` confirms AND after the backend session POST completes. Per the recording-lifecycle investigation's own real-device traces (now fixed and verified), this gap is real and measured: `wireTracking()`/`frameDelegate` assignment completed **~0.15-0.19s after** `didStartRecordingTo` in the verified successful runs (e.g. `+0.192s` in the trace analyzed during the recording-fix work). **The recorded `.mov` file's own internal t=0 is `didStartRecordingTo`; `sessionElapsedSeconds == 0` is ~0.15-0.2s later than that.**
- **Consequence**: an event timestamped `sessionElapsedSeconds = 12.0s` corresponds to roughly `12.15-12.2s` into the actual recorded video file, not `12.0s` — a real, small, systematic offset that must be corrected before any "video timestamp navigation" feature (explicitly named in your Phase 11 use cases) seeks to the wrong moment.
- **Recommended fix (design only, not implemented here)**: capture the wall-clock-independent offset once, at `wireTracking()` time, as `videoTimeOffsetSeconds = (time FrameForwarder's sessionStartTime was set) − (time didStartRecordingTo fired)` — both already observable in `CameraCaptureManager`/`AssessmentSessionViewModel` — and store it once per recording (e.g. alongside `PendingAssessmentRecord`), so a later "jump to this event in the video" feature computes `videoSeekTime = event.startTimestamp + videoTimeOffsetSeconds`. This is a small, well-scoped addition, not a redesign of the recording pipeline, and does not touch the verified recording architecture itself — it only ADDS a timestamp bookkeeping value alongside it.
- Until this offset is captured and applied, any feature that seeks into the video using a raw event timestamp will be measurably wrong by a small, non-negligible amount and must not be shipped as precise navigation.

---

## 13. Future receive/touch/dribble/pass/shot mapping (design only — none implemented in V2)

| Football event | Possible V2-event evidence | Required missing signal(s) |
|---|---|---|
| **Receive** | `ballApproaching` → `ballEnteredNearZone` → (ball velocity direction/magnitude changes sharply, or `ballAccompanyingPlayer`/`ballMovedWithPlayer` begins) | Ball velocity exposure (§6 path A) to detect the velocity-relationship change at the moment of receipt; without it, only geometric entry-into-near-zone is available, which is a much weaker "receive" signal. |
| **Touch** | Near relationship active + an abrupt ball trajectory/velocity change while near | Ball velocity exposure (mandatory — a "trajectory change" is meaningless without a real ball velocity signal); pose-based foot proximity (ankle keypoints already tracked, but foot-to-ball distance is not currently computed anywhere) would meaningfully strengthen this. |
| **Dribble/carry** | `NEAR_SUSTAINED` + `sustainedMovement` + `ballAccompanyingPlayer`/`ballMovedWithPlayer` sustained | Ball velocity exposure for genuine co-movement (§6 path A); without it, only the weaker "co-presence + stable distance while moving" proxy (§6 path B) is available. |
| **Pass** | Near → `ballSeparating` → high ball speed → trajectory away from the player, ideally toward another tracked person | Ball velocity exposure (mandatory, to measure "high speed" and "trajectory"); multi-player tracking/identity (capability audit §5 — still not implemented) to say "toward another PLAYER" rather than just "away." |
| **Shot** | Near → abrupt, high-speed separation → trajectory toward a known goal location | Ball velocity exposure (mandatory); field calibration/goal-location detection (capability audit §7 — not implemented, no field geometry exists at all today). |

None of these are implemented, named, or scaffolded in V2 — this table exists only to show that V2's low-level events are the right SHAPE of building block for them later, contingent on the explicitly-listed missing signals.

---

## 14. False-positive analysis

| Event | False-positive scenario 1 | Scenario 2 | Scenario 3 | Mitigation |
|---|---|---|---|---|
| `movementStarted`/`movementStopped` | Gimbal correction alone moves the image-space box while the player is physically still | Detector jitter briefly crosses the speed threshold | Player briefly exits and re-enters frame (box disappears/reappears) | V1's own 3-frame hysteresis + V2's additional 0.5s hold; `.uncertain` (from a tracking gap) immediately voids the candidate rather than bridging it |
| `sustainedMovement` | Player pacing in a small area registers as continuous low-level motion | Camera slowly panning to keep a slow-moving player centered | Repeated brief moving/stopping ties never individually reach the 3s minimum but a long noisy stretch might | Long minimum duration (3s) + requiring UNbroken `.moving` (any drop resets) makes sporadic noise unable to accumulate |
| Direction change | Gimbal correction reverses direction independent of the player | Player naturally curves gradually (many small angle changes) rather than one sharp turn | Tracking briefly flickers during a real turn, producing a spurious large angle jump between two otherwise-unrelated windows | 45° minimum + window-averaged (not frame-to-frame) angle + confidence floor over both windows + full-`locked` requirement for both windows (a flicker drops confidence and voids the candidate) |
| `ballApproaching` | Camera pans and the ball's image-space position drifts relative to the player without either object truly moving closer | Ball detector jumps between two ball-like objects (a cone, a real ball) creating a spurious trend | A defender's leg is momentarily misdetected as the ball while genuinely closer | Distance is RELATIVE (§7 — camera-motion-invariant argument) so pure camera pan cancels out; monotonic-with-tolerance + minimum total delta rejects a single-sample jump; low classical-detector confidence on a misdetected object is likely (though not guaranteed) to sit near the 0.20 floor, reducing (not eliminating) this risk |
| `ballEnteredNearZone` | Ball passes near the player in transit without any interaction (e.g. rolls past) | Detector briefly locks onto a decoy near the player (cone/sock — a documented `ClassicalCVBallDetector` limitation) | Camera zoom/framing change alters the player's box width, shifting the body-width-relative near threshold | This event is explicitly geometric-only and is NOT claimed to be a touch/possession — the false-positive risk here is in what a FUTURE classifier does with it, not in the geometric fact itself, which is accurately measured; classical-detector decoys remain the one real, undocumented-here risk inherited from the existing detector's own known limitation (capability audit) |

---

## 15. Deterministic test plan (design only — not written yet)

**Movement**: stationary→moving; moving→stationary; threshold jitter (alternating just-below/above, must not fire); temporary tracking loss mid-candidate (must void, not pause); slow movement below threshold (never fires); movement resumes after a stop (fires `movementStarted` again, independently).

**Ball**: constant far distance (no approach event); clean monotonic approach; noisy approach within tolerance (still fires); noisy approach exceeding tolerance (must not fire); near entry; sustained near (fires `ballStayedNear` once, not repeatedly); near→away; detector dropout mid-window (voids the approach/separation candidate; does not itself break an already-`NEAR_SUSTAINED` state early); low confidence (below 0.20 floor — excluded from windows entirely); ball passes near briefly (below `ballStayedNear`'s minimum duration — only `ballEnteredNearZone`/`ballLeftNearZone` fire, not `ballStayedNear`); ball disappears while near (state machine per §9, does not silently keep counting toward sustained).

**Direction**: true ≥45° change while moving (fires); noise while stationary (never evaluated — speed floor gate); small angle variation below 45° (never fires); tracking loss during the candidate window (voids it).

**Session**: reset between players clears the event timeline and all in-progress candidates (mirrors V1's own reset test); assessment ending mid-event discards the open candidate without a synthetic end; long assessment (5,000+ samples) still produces correctly-ordered, non-duplicated events (mirrors V1's own bounded-history-vs-full-session test); event timestamps are strictly non-decreasing; no duplicate events (same type, overlapping/identical time range) ever appear.

---

## 16. Performance requirement

Every input to V2 is a value already computed by V1/TrackingCoordinator (or a small, equally cheap extension of it per §3/§6). V2 itself is proposed as pure arithmetic + small fixed-size window buffers per event type (comparable in shape to V1's own hysteresis state) — no Vision/CoreML/CIContext, no camera/AVFoundation access, no image data at all. Expected per-call cost is the same order of magnitude as V1's own measured ~16µs/call (§H of the V1 report) — negligible against the CV pipeline's own ≤15Hz/~66ms-per-tick budget.

---

## 17. Recommended V2.0 event subset

**IMPLEMENT IN V2.0** (fully supported by existing V1 outputs, or a trivial, already-computed-value extension):
- `movementStarted` / `movementStopped` / `sustainedMovement` (consuming V1's `movementState` directly, §3)
- `ballEnteredNearZone` / `ballLeftNearZone` / `ballStayedNear` (consuming V1's `ballProximityState` directly, §5)
- `ballApproaching` / `ballSeparating` (consuming V1's already-computed `normalizedPlayerBallDistance`, once it is also surfaced on the live path — a trivial extension of the existing V1 struct, not new logic, §4)
- `playerTemporarilyLost` / `playerReacquired` / `ballBecameVisible` / `ballBecameUnavailable` (trivial point-events on V1's own existing `trackingState`/`ballVisible` transitions — not separately designed above because they need no new rule beyond "state changed")

**DEFER TO V2.1** (needs a small, well-justified extension to a component OTHER than pure V1 output — a "new derived signal," not new CV):
- Direction change (needs V1/TrackingCoordinator to retain velocity ANGLE, not just magnitude — §3)
- `ballMovedWithPlayer` / co-movement under its real name (needs `BallTracker.currentVelocity` exposed publicly — §6, path A)
- Video-timestamp offset correction for precise event-to-video navigation (§12) — a real, measured gap that should be fixed before shipping "jump to this moment in the video" as a coach-facing feature, though events can still be recorded/ordered correctly without it meanwhile

**REQUIRES NEW CV** (out of scope for both V2.0 and V2.1):
- Any camera-motion-compensated absolute speed/direction (needs real gimbal-orientation telemetry integration, §7)
- Multi-player-aware events (pass "to a player", pressure, teammate/opponent distinction — capability audit §5, unchanged)
- Field-relative events (shot toward goal, real distance/speed — capability audit §7, unchanged)
- Touch/receive detection needing foot-to-ball proximity (pose ankle keypoints exist but foot-to-ball distance is not currently computed anywhere)

**DO NOT CLAIM YET** (name and concept both withheld until the above ships and is validated against real footage):
- receive, touch, dribble, pass, shot, possession, control (§13 — design sketch only)
- Any event confidence expressed as a single fabricated number rather than the `.high`/`.medium`/`.low` evidence-quality label (§8)
- `ballAccompanyingPlayer`-style proxy under the stronger name `ballMovedWithPlayer` (§6) — ship the weaker proxy, if at all, only under an honestly weaker name

---

Not implemented. Not tested. Not committed. Awaiting review.
