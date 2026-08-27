# TrainingBuddy security baseline

TrainingBuddy handles children’s identity, performance records, and video. Real
child data must only be used in a production deployment that passes
`python -m scripts.production_check` and the complete CI workflow.

## Required controls

- Use one isolated deployment and database per academy. The current release is
  not a shared multi-academy SaaS and production rejects that configuration.
- PostgreSQL with encrypted connections and private-network access.
- Private S3/R2 bucket; public bucket access must remain disabled.
- HTTPS-only cookies and HSTS at the public endpoint.
- Unique administrator and reviewer accounts. Never share passwords.
- Guardian consent before uploading video of a minor.
- Human approval before AI analysis can generate training guidance.
- Daily database backups and object-storage versioning/lifecycle policies.
- Quarterly access review and dependency/security scanning on every change.

## Incident response

If child data may have been exposed, disable affected sessions and storage
credentials, preserve audit logs, identify affected players and guardians, and
obtain legal/privacy guidance before notifications. Do not erase evidence while
investigating.
