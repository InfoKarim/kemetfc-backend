# Production operations

## Deployment gate

1. CI must pass tests, 85% coverage, migrations, static checks, dependency audit,
   and the container build.
2. Run `python -m alembic upgrade head` before starting web or worker services.
3. Run `python -m scripts.production_check` with production environment values.
4. Verify `/health` and `/health/ready` through HTTPS.

## Backups

- Enable daily PostgreSQL backups and test a restore into an isolated database
  every month.
- Enable S3/R2 versioning and lifecycle rules matching `DATA_RETENTION.md`.
- Store backup credentials separately from application credentials.

## Monitoring

Alert on readiness failures, HTTP 5xx rate, login lockouts, queued job age,
failed analysis jobs, worker restarts, database saturation, and object-storage
errors. Logs must include request/job IDs but never passwords, session tokens,
video bytes, or raw child profiles.

## Worker recovery

Job claiming is atomic. A failed job may be requeued only while attempts remain.
Before increasing worker count, verify PostgreSQL is being used and watch queue
age and duplicate analysis IDs.
