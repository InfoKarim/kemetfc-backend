# TrainingBuddy PostgreSQL setup

SQLite remains available for local development and unit tests. PostgreSQL is
required for production.

## Local PostgreSQL

1. Start Docker Desktop.
2. Start the database:

   ```bash
   docker compose up -d postgres
   ```

3. Configure the application shell:

   ```bash
   export APP_ENV=development
   export DATABASE_URL='postgresql+psycopg://trainingbuddy:trainingbuddy_dev@127.0.0.1:5432/trainingbuddy?sslmode=disable'
   ```

4. Create or update the schema and verify the connection:

   ```bash
   python -m alembic upgrade head
   python scripts/check_database.py
   python scripts/smoke_test_postgresql.py
   ```

5. Create the first administrator in a new database:

   ```bash
   python scripts/create_admin.py --username karim
   ```

## Copy an existing SQLite database

Keep the PostgreSQL target empty except for the Alembic schema. First perform a
dry run, then execute the transactional copy:

```bash
export POSTGRES_DATABASE_URL='postgresql+psycopg://trainingbuddy:trainingbuddy_dev@127.0.0.1:5432/trainingbuddy?sslmode=disable'
python scripts/migrate_sqlite_to_postgresql.py
python scripts/migrate_sqlite_to_postgresql.py --execute
```

Users are copied, but active authentication sessions are intentionally omitted.
If any insert or count verification fails, PostgreSQL rolls back the entire copy.

## Production requirements

Set all of the following in the hosting environment:

```bash
APP_ENV=production
DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE?sslmode=require'
AUTH_COOKIE_SECURE=true
```

Use `sslmode=verify-full` when the database provider supplies a trusted CA and
hostname verification. Never use the development credentials from
`compose.yaml` in production.

The application refuses to start in production when PostgreSQL, TLS, or secure
authentication cookies are not configured.
