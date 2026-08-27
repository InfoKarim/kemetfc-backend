from pathlib import Path
import sys

from sqlalchemy import text


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import engine


def main() -> int:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except Exception as error:
        print(f"Database check failed: {error}")
        return 1

    print(f"Database dialect: {engine.dialect.name}")
    print(f"Alembic revision: {revision or 'not initialized'}")
    print("Database check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
