# Child-data retention and deletion

Default operational policy (confirm with counsel for each operating region):

- Original player videos: retain for at most 90 days after approved analysis,
  unless a guardian explicitly renews consent for a documented purpose.
- Raw pose landmarks and analysis artifacts: retain for at most 30 days after
  human review. Keep only approved aggregate scores where possible.
- Training plans and progress history: retain while the player account is active
  and consent remains valid.
- Audit and deletion-request records: retain for the legally required period,
  without retaining deleted video or profile content.
- Backups: 35-day rolling retention, encrypted and access logged.

Deletion requests move from `pending` to `in_review` only after identity
verification. Setting the request to `completed` removes associated videos,
analysis artifacts, plans, consent/link records, and anonymizes the remaining
player key so the legal request record remains referentially valid. The same
completion also removes the child's assessments and derived metrics, forecasts
(predictions, simulations, outcomes), coach message, tracking sessions with
their telemetry, events, features and coach labels, and any ML-dataset
candidate entries tied to the removed videos or consents. Every check-in token for the
child is revoked and the player's photo reference is cleared (the image file is
not deleted by this step). Billing and registration rows are not touched by this
step (policy decision pending). Which tables are exported, deleted or retained,
and why, is recorded in `app/privacy_classification.py`, and a test fails when a
new player-linked table is not classified. V1 assessment data (assessments, station runs, attempts, manual times and
their full correction history) is exported and, on completed deletion, removed in
dependency order; normal use has no delete path for it. The guardian export lists what it
contains and what it leaves out; it is not a complete copy of everything held.

