"""Migration d4b7e9a1c635 (V1 foundation): clean build, build over an existing legacy
schema with data, additive-only behaviour, seed equals code, schema matches the models.

Every database here is a throwaway SQLite file under pytest's tmp_path.
"""
import subprocess
import sys
from datetime import date, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

import app.db_models  # noqa: F401  (registers every model on Base.metadata)
from app.database import Base
from app.v1.definitions import METRIC_DEFINITIONS
from tests.test_migrations import PROJECT_ROOT, migration_environment

PREVIOUS_HEAD = "c9e1a4b7d302"
V1_HEAD = "d4b7e9a1c635"
V1_TABLES = {
    "v1_metric_definitions", "v1_assessments", "v1_station_runs", "v1_trials",
    "v1_manual_measurements", "v1_manual_measurement_corrections",
}


def alembic(url, *args):
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=PROJECT_ROOT,
                          env=migration_environment(url), check=True, capture_output=True, text=True)


def legacy_schema(engine):
    inspector = inspect(engine)
    return {
        name: (
            [(c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(name)],
            sorted(str(fk["constrained_columns"]) + fk["referred_table"] for fk in inspector.get_foreign_keys(name)),
            sorted(ix["name"] for ix in inspector.get_indexes(name)),
        )
        for name in inspector.get_table_names() if not name.startswith("v1_")
    }


def test_migration_applies_on_a_clean_scratch_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'clean.db'}"
    alembic(url, "upgrade", "head")
    engine = create_engine(url)
    assert V1_TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == V1_HEAD


def test_seeded_definitions_equal_the_code_constants(tmp_path):
    url = f"sqlite:///{tmp_path / 'seed.db'}"
    alembic(url, "upgrade", "head")
    with create_engine(url).connect() as connection:
        rows = connection.execute(text("SELECT * FROM v1_metric_definitions ORDER BY metric_id")).mappings().all()
    assert len(rows) == len(METRIC_DEFINITIONS) == 3
    expected = {m.metric_id: m for m in METRIC_DEFINITIONS}
    for row in rows:
        definition = expected[row["metric_id"]]
        for field in definition.__dict__:
            assert bool(row[field]) == definition.is_active if field == "is_active" else row[field] == getattr(definition, field), field
    assert {r["unit"] for r in rows} == {"seconds"} and {r["measurement_source"] for r in rows} == {"HUMAN_MANUAL"}
    # Nothing beyond the frozen manual-timing metrics: no 10 m split, no video timing, no scores.
    assert not [r for r in rows if "010M" in r["metric_id"] or "005M" in r["metric_id"]]


def test_migrated_schema_matches_the_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'drift.db'}"
    alembic(url, "upgrade", "head")
    with create_engine(url).connect() as connection:
        diffs = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert [(d[0], getattr(d[1], "name", None)) for d in diffs if "v1_" in repr(d)] == []


def test_migration_check_constraints_exist_in_the_migrated_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'checks.db'}"
    alembic(url, "upgrade", "head")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (user_id, username, password_hash, role, active, failed_login_attempts, created_at, updated_at) VALUES ('U1','u','x','coach',1,0,'2026-01-01','2026-01-01')"))
        connection.execute(text("INSERT INTO players (player_id, first_name_ar, last_name_ar, first_name_en, last_name_en, date_of_birth, sex, physical_profile, technical_profile, mental_profile, match_performance, source) VALUES ('P1','a','b','c','d','2016-01-01','male','{}','{}','{}','{}','manual')"))
        connection.execute(text("INSERT INTO v1_assessments VALUES ('A1','P1',NULL,'2026-09-12','KEMET_V1','S','IN_PROGRESS','U1','2026-09-12',NULL)"))
        connection.execute(text("INSERT INTO v1_station_runs VALUES ('R1','A1','S1','U1','2026-09-12')"))
        connection.execute(text("INSERT INTO v1_trials VALUES ('T1','R1','',1,'VALID',NULL,'U1','2026-09-12')"))
    bad_rows = [
        "INSERT INTO v1_trials VALUES ('T2','R1','',2,'INVALID',NULL,'U1','2026-09-12')",
        "INSERT INTO v1_trials VALUES ('T3','R1','',0,'VALID',NULL,'U1','2026-09-12')",
        "INSERT INTO v1_trials VALUES ('T4','R1','',1,'VALID',NULL,'U1','2026-09-12')",
        "INSERT INTO v1_manual_measurements VALUES ('M1','T1','PHYS-SPR-020M-MAN-001','1.0.0','RECORDED',0,'seconds',NULL,'HUMAN_MANUAL','MANUAL_STOPWATCH','U1','2026-09-12',NULL)",
        "INSERT INTO v1_manual_measurements VALUES ('M2','T1','PHYS-SPR-020M-MAN-001','1.0.0','RECORDED',425,'seconds',NULL,'AI','MANUAL_STOPWATCH','U1','2026-09-12',NULL)",
        "INSERT INTO v1_manual_measurements VALUES ('M3','T1','PHYS-SPR-020M-MAN-001','1.0.0','NOT_RECORDED',0,'seconds','TIMER_FAILURE','HUMAN_MANUAL','MANUAL_STOPWATCH','U1','2026-09-12',NULL)",
    ]
    for statement in bad_rows:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text(statement))
    with engine.begin() as connection:  # and a good row still goes in
        connection.execute(text("INSERT INTO v1_manual_measurements VALUES ('M9','T1','PHYS-SPR-020M-MAN-001','1.0.0','RECORDED',425,'seconds',NULL,'HUMAN_MANUAL','MANUAL_STOPWATCH','U1','2026-09-12',NULL)"))
        assert connection.execute(text("SELECT value_centiseconds FROM v1_manual_measurements")).scalar_one() == 425


def test_migration_applies_over_an_existing_schema_without_touching_legacy_data(tmp_path):
    url = f"sqlite:///{tmp_path / 'existing.db'}"
    alembic(url, "upgrade", PREVIOUS_HEAD)
    engine = create_engine(url)
    assert not V1_TABLES & set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO users (user_id, username, password_hash, role, active, failed_login_attempts, created_at, updated_at) VALUES ('U1','u','x','coach',1,0,'2026-01-01','2026-01-01')"))
        connection.execute(text("INSERT INTO players (player_id, first_name_ar, last_name_ar, first_name_en, last_name_en, date_of_birth, sex, physical_profile, technical_profile, mental_profile, match_performance, source) VALUES ('P1','a','b','Legacy','Kid','2016-01-01','male','{}','{}','{}','{}','manual')"))
        connection.execute(text("INSERT INTO player_assessments (assessment_id, player_id, pillar, test_category, test_type, methodology_version, test_date, age_at_assessment_years, raw_data, calculated_metrics, ai_assisted, created_at) VALUES ('LEG1','P1','physical','speed','sprint','legacy','2026-01-01',10,'{\"overall_score\": 77}','{}',0,'2026-01-01')"))
        connection.execute(text("INSERT INTO player_metrics (player_id, assessment_id, metric_name, category, metric_score, recorded_at, source, created_at) VALUES ('P1','LEG1','speed','physical',71.5,'2026-01-01','legacy','2026-01-01')"))
    tables = ("users", "players", "player_assessments", "player_metrics")

    def rows():
        with engine.connect() as connection:
            return {t: [tuple(r) for r in connection.execute(text(f"SELECT * FROM {t} ORDER BY 1"))] for t in tables}

    rows_before, schema_before = rows(), legacy_schema(engine)
    alembic(url, "upgrade", "head")
    assert V1_TABLES <= set(inspect(engine).get_table_names())
    assert rows() == rows_before                 # legacy rows identical, nothing converted
    assert legacy_schema(engine) == schema_before  # no legacy table, column, key or index changed
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM v1_assessments")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM v1_manual_measurements")).scalar_one() == 0


def test_downgrade_removes_only_the_v1_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'down.db'}"
    alembic(url, "upgrade", "head")
    engine = create_engine(url)
    with_v1 = legacy_schema(engine)
    alembic(url, "downgrade", PREVIOUS_HEAD)
    assert not V1_TABLES & set(inspect(engine).get_table_names())
    assert legacy_schema(engine) == with_v1
    alembic(url, "upgrade", "head")  # and it re-applies cleanly
    assert V1_TABLES <= set(inspect(create_engine(url)).get_table_names())
