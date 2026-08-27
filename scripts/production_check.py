"""Fail-fast validation for a TrainingBuddy production deployment."""

from app.config import get_database_url, validate_production_settings
from app.database import engine
from app.migration_health import require_database_at_head


def main() -> int:
    database_url = get_database_url()
    validate_production_settings(database_url)

    with engine.connect() as connection:
        revisions = require_database_at_head(connection)

    print(
        "production settings valid; database revisions="
        + ",".join(revisions)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
