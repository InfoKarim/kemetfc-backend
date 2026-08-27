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
player key so the legal request record remains referentially valid.

