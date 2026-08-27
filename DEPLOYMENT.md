# TrainingBuddy staging and production deployment

The repository includes a Render Blueprint (`render.yaml`) for one web service,
one background analysis worker, and managed PostgreSQL. Private videos use a
Cloudflare R2 bucket through its S3-compatible API.

This release supports a managed **single-academy deployment**. Each academy
must receive a separate web service, worker, database, and storage credentials.
Do not place unrelated academies in the same deployment until organization
tenancy and row-level authorization have been implemented and audited.

## Before deployment

1. Push this repository to a private GitHub or GitLab repository.
2. Create a private R2 bucket with public access blocked.
3. Create an R2 Object Read & Write token restricted to that bucket.
4. In Render, create a Blueprint from the repository and review the displayed
   service and database costs before approving creation.

The Blueprint prompts for secrets. Enter them in Render, never in Git:

- `DATABASE_URL`: use the Render PostgreSQL external URL and append
  `?sslmode=require` (or `&sslmode=require` if it already has query options).
- `S3_VIDEO_BUCKET`: the private R2 bucket name.
- `S3_ENDPOINT_URL`: `https://ACCOUNT_ID.r2.cloudflarestorage.com`.
- `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`: the bucket-scoped R2 token.

Enter the same storage and database values for the web and worker services.
The application validates PostgreSQL TLS, secure cookies, and private object
storage before it starts in production.

The production preflight also compares the connected database revision with
every Alembic head. A stale or partially migrated database fails deployment
before the web service accepts traffic.

## Verification

After the first deploy:

1. Confirm `/health` and `/health/ready` return HTTP 200 over HTTPS.
2. Run `python scripts/create_admin.py --username karim` from a Render shell.
3. Upload a short consented training video.
4. Confirm the analysis job progresses from queued to processing to completed.
5. Confirm the saved analysis and draft training plan appear in the player's
   Development Snapshot.
6. Confirm an authenticated video request produces a short-lived R2 URL.

## Recovery and monitoring

- Use a paid PostgreSQL plan with point-in-time recovery for production.
- Schedule logical database exports to separate object storage for long-term
  retention, and test restores into an empty database every quarter.
- Enable Render notifications for failed deploys and unhealthy services.
- Alert on repeated `analysis_worker_iteration_failed`, HTTP 5xx responses,
  failed jobs, database saturation, and R2 authentication failures.
- Configure a custom domain in Render; Render provisions and renews TLS and
  redirects HTTP to HTTPS.

Do not scale the current database-backed analysis worker beyond one instance.
Multiple workers require PostgreSQL row locking (`FOR UPDATE SKIP LOCKED`) or a
dedicated queue before horizontal scaling.
