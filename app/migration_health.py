"""Database migration checks used by production startup and readiness probes."""

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


class DatabaseSchemaNotCurrent(RuntimeError):
    """Raised when the connected database is not at every Alembic head."""


def expected_migration_heads(
    alembic_ini: Path = DEFAULT_ALEMBIC_INI,
) -> tuple[str, ...]:
    config = Config(str(alembic_ini))
    script = ScriptDirectory.from_config(config)
    return tuple(sorted(script.get_heads()))


def current_migration_heads(connection: Connection) -> tuple[str, ...]:
    context = MigrationContext.configure(connection)
    return tuple(sorted(context.get_current_heads()))


def require_database_at_head(
    connection: Connection,
    alembic_ini: Path = DEFAULT_ALEMBIC_INI,
) -> tuple[str, ...]:
    expected = expected_migration_heads(alembic_ini)
    current = current_migration_heads(connection)

    if current != expected:
        current_label = ", ".join(current) if current else "none"
        expected_label = ", ".join(expected) if expected else "none"
        raise DatabaseSchemaNotCurrent(
            "Database migration mismatch: "
            f"current={current_label}; expected={expected_label}. "
            "Run 'python -m alembic upgrade head'."
        )

    return current
