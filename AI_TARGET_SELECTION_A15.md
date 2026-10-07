# A1.5 - the coach selects the player in the video; identity is never guessed

Roster selection answers "whose assessment is this?". It does not answer "which person in the video is that
player?". The coach answers that by tapping the player on a frame of the video. The AI then follows only that
person, for as long as it can prove it is the same person, and stops asking nothing of anyone else.

```
PLAYER ASSESSMENT -> roster player -> Record or Upload -> Save -> SELECT PLAYER IN VIDEO (tap, CONFIRM)
   -> [Mac/server, later] people on every frame -> follow the selection (target_tracking) -> pose on LOCKED frames
```

No face recognition, no biometric identity. The only things used are where a person is, how big the box is, and
a colour histogram of the clothing on the torso. The selection is a temporary tracking identity for that video
(`TARGET-1`, `TEST_TARGET`), never a roster id.

## Identity states (per frame)

| state | meaning | player-specific analysis |
|---|---|---|
| LOCKED | followed with enough evidence | allowed |
| UNCERTAIN | a short doubt: detector miss under 0.5 s, someone overlapping, two equally likely candidates | blocked |
| LOST | not visible for 0.5 s or more | blocked; never recovers by itself |
| RESELECTION_REQUIRED | continuity cannot be proven | blocked; only a new coach tap leaves this state |

What puts a frame in RESELECTION_REQUIRED: a picture cut (the cut frame and everything after, until the coach taps
again); the target left the picture and someone appears; the target was gone longer than 1.5 s; a person who looks
like the target (same clothing colours) was near while it was in doubt; a doubt that lasts over 1 s. A person who
reappears after LOST is never adopted automatically.

Recovery from UNCERTAIN is allowed only for a short gap, with clothing agreement of at least 0.85, a clear margin over
every other candidate, no overlap, no look-alike nearby, and the person back within a quarter of a frame height of
where they were. Neighbours seen next to the target are followed too, so when the target vanishes they are not
mistaken for it. Colour cannot tell two children in the same kit apart, so a same-kit neighbour always means "ask the
coach", never "guess".

Evidence is reported as segments (state, epoch, start, end, reason). Measurements use only an unbroken run of LOCKED
frames inside one epoch (one selection): no smoothing, derivative, cadence window or asymmetry window crosses a
UNCERTAIN, LOST or RESELECTION_REQUIRED frame, a picture cut, or the start of a new selection.

## Tapping

* `target_selection.resolve_tap` (Mac) and `VideoTargetPicker.personIndex` (iPhone) are the same rule: the person whose
  box CONTAINS the tap. Overlap: closest box centre measured in the box's own size, then the smaller box, then higher
  confidence, then lower index. A tap not on anyone selects no one ("No player detected at that point. Tap directly
  on the player."); the nearest person is never chosen.
* On the phone the frame is decoded upright (`appliesPreferredTrackTransform`), shown aspect-fit, and the tap goes
  through `VideoTargetGeometry.normalizedPoint`, which uses the upright size (stored size with the rotation applied),
  centres the picture inside the safe area, and returns nil for a tap in a letterbox band or an inset.
* The frame offered first is the best of 12 sampled frames (people visible, confident, large enough, not dark, not
  blurred), not frame 0. The coach can scrub to any other frame.

## What is stored, and where

The confirmed selection is written onto the local pending record (`targetSelections`, optional). No server or database
schema is involved on the phone. The Mac pipeline takes a selection as `--selection-json`. To give the server-side
pipeline the same selection, something on the server must hold it (a new column or table, or a metadata field): that
is a schema decision and has not been made or built.

## Not in this phase

Pose and angles on a real player, ratings, physical speed, ball analysis. The A1 "most prominent person" choice remains
only as `--diagnostic-most-prominent`, labelled `DIAGNOSTIC_MOST_PROMINENT`, `identity_verified_by_coach: false`; a
player-assessment run without a coach selection refuses.

## Known limits

* Cut detection uses the colour histogram of the whole frame (threshold 0.75, calibrated on one reel). A dissolve, or two
  cuts between very similar shots, can be missed; the clothing and geometry guards are the second line.
* A detector box that merges two touching children into one box can look like a single person for a few frames before the
  overlap rule fires.
* On a real play clip the guards are deliberately strict: three children in one kit near each other leave very few
  LOCKED frames. That is the safe answer, and the coach can tap again.
