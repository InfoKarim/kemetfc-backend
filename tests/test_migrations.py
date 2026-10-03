import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, inspect, text

from app.migration_health import (
    DatabaseSchemaNotCurrent,
    require_database_at_head,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {
    "alembic_version",
    "analyses",
    "assessment_registrations",
    "auth_sessions",
    "audit_events",
    "billing_settings",
    "coach_messages",
    "coach_validation_labels",
    "contact_messages",
    "data_records",
    "drill_diagram_annotations",
    "drills",
    "family_discount_rules",
    "generated_drill_diagrams",
    "guardian_consents",
    "id_counters",
    "guardian_player_links",
    "manual_payments",
    "matches",
    "membership_plans",
    "ml_dataset_entries",
    "ml_model_registry",
    "ml_predictions",
    "monte_carlo_simulations",
    "messages",
    "notifications",
    "password_reset_codes",
    "payments",
    "player_assessments",
    "player_checkin_tokens",
    "player_memberships",
    "player_metrics",
    "players",
    "prediction_outcomes",
    "privacy_requests",
    "promo_code_redemptions",
    "promo_codes",
    "refunds",
    "seasons",
    "stripe_customer_mappings",
    "stripe_events",
    "subscriptions",
    "teams",
    "tracking_events",
    "tracking_feature_sets",
    "tracking_samples",
    "tracking_sessions",
    "training_plans",
    "users",
    "v1_assessments",
    "v1_manual_measurement_corrections",
    "v1_manual_measurements",
    "v1_metric_definitions",
    "v1_station_runs",
    "v1_trials",
    "video_analysis_jobs",
    "videos",
}


def migration_environment(database_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "APP_ENV": "test",
        "AUTH_COOKIE_SECURE": "false",
        "DATABASE_URL": database_url,
    })
    return environment


def test_migrations_build_fresh_sqlite_database(tmp_path):
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path}"

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=migration_environment(database_url),
        check=True,
        capture_output=True,
        text=True,
    )

    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES

    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert revision == "d4b7e9a1c635"
    job_columns = {
        column["name"]
        for column in inspect(engine).get_columns("video_analysis_jobs")
    }
    assert "target_track_id" in job_columns
    user_columns = {
        column["name"]
        for column in inspect(engine).get_columns("users")
    }
    assert "feature_permissions" in user_columns
    assert "email" in user_columns
    player_columns = {
        column["name"]
        for column in inspect(engine).get_columns("players")
    }
    assert "weak_foot_profile" in player_columns
    assert "jersey_number" in player_columns
    metric_columns = {
        column["name"]
        for column in inspect(engine).get_columns("player_metrics")
    }
    assert "metric_name" in metric_columns
    assert "metric_score" in metric_columns
    prediction_columns = {
        column["name"]
        for column in inspect(engine).get_columns("ml_predictions")
    }
    assert "predicted_score" in prediction_columns
    assert "model_scope" in prediction_columns
    simulation_columns = {
        column["name"]
        for column in inspect(engine).get_columns("monte_carlo_simulations")
    }
    assert "p05" in simulation_columns
    assert "p95" in simulation_columns
    assert "uncertainty_level" in simulation_columns
    outcome_columns = {
        column["name"]
        for column in inspect(engine).get_columns("prediction_outcomes")
    }
    assert "prediction_error" in outcome_columns
    assert "absolute_error" in outcome_columns

    with engine.connect() as connection:
        assert require_database_at_head(connection) == ("d4b7e9a1c635",)


def test_migration_health_rejects_database_without_migrations(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")

    with engine.connect() as connection:
        with pytest.raises(DatabaseSchemaNotCurrent, match="upgrade head"):
            require_database_at_head(connection)


@pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"),
    reason="requires a real PostgreSQL instance (set POSTGRES_TEST_URL)",
)
def test_migrations_build_fresh_postgresql_database():
    database_url = os.environ["POSTGRES_TEST_URL"]

    def run_alembic(*args: str) -> None:
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=PROJECT_ROOT,
            env=migration_environment(database_url),
            check=True,
            capture_output=True,
            text=True,
        )

    engine = create_engine(database_url)

    # Start from a clean slate: earlier tests (or a re-run) may have left
    # this database at head already.
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.commit()

    run_alembic("upgrade", "head")

    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES

    with engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert require_database_at_head(connection) == (revision,)

    job_columns = {
        column["name"]
        for column in inspect(engine).get_columns("video_analysis_jobs")
    }
    assert "target_track_id" in job_columns
    user_columns = {
        column["name"]
        for column in inspect(engine).get_columns("users")
    }
    assert "feature_permissions" in user_columns
    assert "email" in user_columns

    # Round-trip every migration down and back up for real against
    # PostgreSQL — this is the part `alembic upgrade head --sql` (a purely
    # offline SQL dump) can never prove, since it never executes anything.
    run_alembic("downgrade", "base")
    # alembic's own bookkeeping table survives a downgrade to "base" (it
    # just becomes empty) — every table our migrations created should not.
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT * FROM alembic_version")).all() == []

    run_alembic("upgrade", "head")
    assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES


def test_postgresql_migration_sql_has_single_drills_table():
    database_url = (
        "postgresql+psycopg://user:password@localhost/trainingbuddy"
        "?sslmode=disable"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=PROJECT_ROOT,
        env=migration_environment(database_url),
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.count("CREATE TABLE drills") == 1
    assert result.stdout.count("CREATE TABLE videos") == 1
    assert "CREATE TABLE users" in result.stdout
    assert "CREATE TABLE guardian_consents" in result.stdout
    assert "CREATE TABLE audit_events" in result.stdout
    assert "CREATE TABLE guardian_player_links" in result.stdout
    assert "CREATE TABLE privacy_requests" in result.stdout


# ---------------------------------------------------------------------------
# Tracking telemetry idempotency (tests-first: the revision does not exist yet)
#
# Expected schema change, two additive revisions on top of d5f9a2c74e18:
#   tracking_samples.sequence_number        INTEGER NULL
#   tracking_events.client_event_id         VARCHAR NULL
#   tracking_events.client_occurred_at      DATETIME NULL
#   UNIQUE INDEX (session_id, sequence_number)   on tracking_samples
#   UNIQUE INDEX (session_id, client_event_id)   on tracking_events
# Nullable columns: existing rows and pre-upgrade clients keep working,
# because NULLs never collide in a unique index (SQLite and PostgreSQL).
# ---------------------------------------------------------------------------

PRE_IDEMPOTENCY_REVISION = "d5f9a2c74e18"   # the head before the two telemetry revisions


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=migration_environment(database_url),
        check=True,
        capture_output=True,
        text=True,
    )


def _column_names(engine, table: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table)}


def test_tracking_idempotency_migration_preserves_legacy_rows_and_enforces_uniqueness(tmp_path):
    from sqlalchemy.exc import IntegrityError

    database_url = f"sqlite:///{tmp_path / 'idempotency.db'}"
    _alembic(database_url, "upgrade", PRE_IDEMPOTENCY_REVISION)
    engine = create_engine(database_url)

    # A "deployed" database: a session with a legacy sample and event.
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tracking_sessions (session_id, player_id, coach_user_id, tracking_mode, "
            "calibration_status, status, started_at, ball_model_status, created_at) VALUES "
            "('S1', 'P1', 'U1', 'smart_soccer', 'uncalibrated', 'recording', "
            "'2026-01-01 00:00:00', 'missing', '2026-01-01 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO tracking_samples (session_id, t_seconds, gimbal_state, tracking_mode, tracking_status) "
            "VALUES ('S1', 0.5, 'tracking_active', 'smart_soccer', 'locked')"
        ))
        connection.execute(text(
            "INSERT INTO tracking_events (event_id, session_id, occurred_at, event_type, details) "
            "VALUES ('E1', 'S1', '2026-01-01 00:00:00', 'mode_switch', '{}')"
        ))
    assert "sequence_number" not in _column_names(engine, "tracking_samples")

    _alembic(database_url, "upgrade", "head")

    assert {"sequence_number", "identity_ambiguous"} <= _column_names(engine, "tracking_samples")
    assert {"client_event_id", "client_occurred_at"} <= _column_names(engine, "tracking_events")
    with engine.connect() as connection:
        # Existing rows are preserved and simply have no identity yet.
        assert connection.execute(text("SELECT count(*), max(sequence_number) FROM tracking_samples")).one() == (1, None)
        assert connection.execute(text("SELECT identity_ambiguous FROM tracking_samples")).scalar_one() is None, "legacy rows stay unknown"
        assert connection.execute(text("SELECT client_event_id FROM tracking_events")).scalar_one() is None

    def insert_sample(connection, sequence_number):
        connection.execute(text(
            "INSERT INTO tracking_samples (session_id, sequence_number, t_seconds, gimbal_state, tracking_mode, "
            "tracking_status) VALUES ('S1', :seq, 1.0, 'tracking_active', 'smart_soccer', 'locked')"
        ), {"seq": sequence_number})

    # The database itself refuses a duplicate (session, sequence)...
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            insert_sample(connection, 1)
            insert_sample(connection, 1)
    # ...but allows the same sequence in another session and any number of NULLs.
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tracking_sessions (session_id, player_id, coach_user_id, tracking_mode, "
            "calibration_status, status, started_at, ball_model_status, created_at) VALUES "
            "('S2', 'P1', 'U1', 'smart_soccer', 'uncalibrated', 'recording', "
            "'2026-01-01 00:00:00', 'missing', '2026-01-01 00:00:00')"
        ))
        insert_sample(connection, 1)
        connection.execute(text(
            "INSERT INTO tracking_samples (session_id, sequence_number, t_seconds, gimbal_state, tracking_mode, "
            "tracking_status) VALUES ('S2', 1, 1.0, 'g', 'smart_soccer', 'locked')"
        ))
        insert_sample(connection, None)
        insert_sample(connection, None)

    def insert_event(connection, event_id, client_event_id):
        connection.execute(text(
            "INSERT INTO tracking_events (event_id, session_id, occurred_at, event_type, details, client_event_id) "
            "VALUES (:event_id, 'S1', '2026-01-01 00:00:00', 'mode_switch', '{}', :client_event_id)"
        ), {"event_id": event_id, "client_event_id": client_event_id})

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            insert_event(connection, "E2", "dup")
            insert_event(connection, "E3", "dup")
    with engine.begin() as connection:
        insert_event(connection, "E4", None)
        insert_event(connection, "E5", None)


def test_tracking_idempotency_migration_downgrade_is_non_destructive(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'downgrade.db'}"
    _alembic(database_url, "upgrade", "head")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO tracking_sessions (session_id, player_id, coach_user_id, tracking_mode, "
            "calibration_status, status, started_at, ball_model_status, created_at) VALUES "
            "('S1', 'P1', 'U1', 'smart_soccer', 'uncalibrated', 'recording', "
            "'2026-01-01 00:00:00', 'missing', '2026-01-01 00:00:00')"
        ))
        connection.execute(text(
            "INSERT INTO tracking_samples (session_id, sequence_number, t_seconds, gimbal_state, tracking_mode, "
            "tracking_status) VALUES ('S1', 7, 0.5, 'tracking_active', 'smart_soccer', 'locked')"
        ))

    _alembic(database_url, "downgrade", PRE_IDEMPOTENCY_REVISION)

    engine = create_engine(database_url)
    assert "sequence_number" not in _column_names(engine, "tracking_samples")
    assert "identity_ambiguous" not in _column_names(engine, "tracking_samples")
    assert "client_event_id" not in _column_names(engine, "tracking_events")
    assert "client_occurred_at" not in _column_names(engine, "tracking_events")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM tracking_samples")).scalar_one() == 1
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == PRE_IDEMPOTENCY_REVISION


def test_postgresql_sql_for_tracking_idempotency_is_additive_and_unique():
    """Offline SQL for PostgreSQL (no server needed), like the drills-table
    test above: the new revision must only ADD nullable columns and UNIQUE
    indexes — no data-destroying statement."""
    database_url = "postgresql+psycopg://user:password@localhost/trainingbuddy?sslmode=disable"

    sql = _alembic(database_url, "upgrade", f"{PRE_IDEMPOTENCY_REVISION}:head", "--sql").stdout

    assert "ALTER TABLE tracking_samples ADD COLUMN sequence_number INTEGER" in sql
    assert "ALTER TABLE tracking_samples ADD COLUMN identity_ambiguous BOOLEAN" in sql
    assert "ALTER TABLE tracking_events ADD COLUMN client_event_id VARCHAR" in sql
    assert "ALTER TABLE tracking_events ADD COLUMN client_occurred_at TIMESTAMP" in sql
    assert "CREATE UNIQUE INDEX" in sql
    assert "ON tracking_samples (session_id, sequence_number)" in sql
    assert "ON tracking_events (session_id, client_event_id)" in sql
    assert "DROP TABLE" not in sql
    assert "DELETE FROM" not in sql
    added_column_statements = [line for line in sql.splitlines() if "ADD COLUMN" in line]
    assert added_column_statements and all("NOT NULL" not in line for line in added_column_statements)


@pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"),
    reason="requires a real PostgreSQL instance (set POSTGRES_TEST_URL)",
)
def test_tracking_idempotency_unique_indexes_exist_on_postgresql():
    database_url = os.environ["POSTGRES_TEST_URL"]
    engine = create_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.commit()

    _alembic(database_url, "upgrade", "head")

    def unique_index_columns(table):
        return {
            tuple(index["column_names"])
            for index in inspect(engine).get_indexes(table)
            if index.get("unique")
        }

    assert ("session_id", "sequence_number") in unique_index_columns("tracking_samples")
    assert ("session_id", "client_event_id") in unique_index_columns("tracking_events")
    _alembic(database_url, "downgrade", PRE_IDEMPOTENCY_REVISION)
    assert "sequence_number" not in _column_names(engine, "tracking_samples")
