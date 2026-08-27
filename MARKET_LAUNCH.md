# TrainingBuddy managed-pilot launch

## Supported commercial release

The first sellable release is a managed, single-academy pilot. It is not a
public multi-tenant SaaS. Each customer receives isolated application,
database, and object-storage resources.

Suggested founding offer: USD 99 per team per month or USD 999 per year, up to
25 players and 10 full-match analyses per month. Additional analysis usage must
be manually approved until measured compute and storage costs are available.

## Technical gates

- CI tests and minimum 85% coverage pass.
- `python -m alembic upgrade head` completes.
- `python -m scripts.production_check` confirms configuration and schema head.
- PostgreSQL TLS, secure cookies, private S3/R2 storage, HTTPS, backups, and
  monitoring are active.
- One academy owns the deployment; no unrelated customer data is present.
- A guardian consent record exists before processing a minor's video.
- Low-quality or unapproved analysis cannot generate training guidance.

## Pilot evidence gates

- Signed pilot agreement and data-processing terms.
- Named academy administrator and privacy contact.
- Consented representative matches from the intended phone/gimbal setup.
- Coach-reviewed annotations and agreement report.
- Locked held-out evaluation with calibration and failure-slice reporting.
- Documented support, deletion, incident, and refund processes.

## Before shared SaaS launch

Implement organizations, memberships, organization IDs on every customer-owned
record, tenant-scoped queries, cross-tenant denial tests, tenant-aware audit
logs, usage metering, billing, and an independent security/privacy review.
