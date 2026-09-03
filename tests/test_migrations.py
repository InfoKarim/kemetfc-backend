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
    "data_records",
    "drills",
    "guardian_consents",
    "id_counters",
    "guardian_player_links",
    "matches",
    "ml_dataset_entries",
    "messages",
    "notifications",
    "password_reset_codes",
    "players",
    "privacy_requests",
    "seasons",
    "teams",
    "training_plans",
    "users",
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

    assert revision == "b6f2d4a19e05"
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

    with engine.connect() as connection:
        assert require_database_at_head(connection) == ("b6f2d4a19e05",)


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
