from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import get_database_url
from app.config import (
    get_database_max_overflow,
    get_database_pool_recycle_seconds,
    get_database_pool_size,
    get_database_pool_timeout_seconds,
    validate_production_settings,
)


DATABASE_URL = get_database_url()
validate_production_settings(DATABASE_URL)


def build_engine_options(database_url: str) -> dict:
    options: dict = {"pool_pre_ping": True}

    if database_url.startswith("sqlite"):
        options["connect_args"] = {
            "check_same_thread": False,
        }
        return options

    options.update({
        "pool_size": get_database_pool_size(),
        "max_overflow": get_database_max_overflow(),
        "pool_timeout": get_database_pool_timeout_seconds(),
        "pool_recycle": get_database_pool_recycle_seconds(),
    })
    return options


engine_options = build_engine_options(DATABASE_URL)


engine = create_engine(
    DATABASE_URL,
    **engine_options,
)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
