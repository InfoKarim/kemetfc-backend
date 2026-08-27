import pytest

from app.database import build_engine_options, engine, get_db


def test_get_db():
    generator = get_db()

    db = next(generator)

    assert db is not None
    assert db.is_active

    generator.close()


def test_sqlite_foreign_keys_enabled():
    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite-specific foreign-key pragma")

    with engine.connect() as connection:
        foreign_keys_enabled = connection.exec_driver_sql(
            "PRAGMA foreign_keys"
        ).scalar()

    assert foreign_keys_enabled == 1


def test_sqlite_engine_options():
    options = build_engine_options("sqlite:///./test.db")

    assert options["pool_pre_ping"] is True
    assert options["connect_args"]["check_same_thread"] is False


def test_postgresql_engine_options():
    options = build_engine_options(
        "postgresql+psycopg://user:password@db/trainingbuddy"
    )

    assert options["pool_pre_ping"] is True
    assert options["pool_size"] > 0
    assert options["max_overflow"] >= 0
    assert options["pool_timeout"] > 0
    assert options["pool_recycle"] > 0
