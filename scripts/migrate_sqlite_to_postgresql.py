import argparse
import os
from pathlib import Path
import sys

from sqlalchemy import MetaData, create_engine, func, select
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


COPY_ORDER = [
    "teams",
    "players",
    "matches",
    "data_records",
    "videos",
    "analyses",
    "drills",
    "training_plans",
    "video_analysis_jobs",
    "users",
]


def row_count(connection, table) -> int:
    return int(connection.execute(select(func.count()).select_from(table)).scalar_one())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy TrainingBuddy data from SQLite to PostgreSQL.",
    )
    parser.add_argument(
        "--source",
        default="sqlite:///./soccer_ai.db",
        help="SQLite source URL",
    )
    parser.add_argument(
        "--target",
        default=os.getenv("POSTGRES_DATABASE_URL"),
        help="PostgreSQL target URL or POSTGRES_DATABASE_URL",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform the copy; otherwise only validate and show counts",
    )
    args = parser.parse_args()

    if not args.target:
        print("--target or POSTGRES_DATABASE_URL is required")
        return 2

    source_url = make_url(args.source)
    target_url = make_url(args.target)

    if source_url.get_backend_name() != "sqlite":
        print("Source must be SQLite")
        return 2

    if target_url.get_backend_name() != "postgresql":
        print("Target must be PostgreSQL")
        return 2

    source_engine = create_engine(source_url)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    source_metadata = MetaData()
    target_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    target_metadata.reflect(bind=target_engine)

    missing_source = set(COPY_ORDER) - set(source_metadata.tables)
    missing_target = set(COPY_ORDER) - set(target_metadata.tables)

    if missing_source or missing_target:
        print(f"Missing source tables: {sorted(missing_source)}")
        print(f"Missing target tables: {sorted(missing_target)}")
        return 1

    with source_engine.connect() as source_connection:
        source_counts = {
            name: row_count(source_connection, source_metadata.tables[name])
            for name in COPY_ORDER
        }

    with target_engine.connect() as target_connection:
        target_counts = {
            name: row_count(target_connection, target_metadata.tables[name])
            for name in COPY_ORDER
        }

    populated_targets = {
        name: count for name, count in target_counts.items() if count
    }

    if populated_targets:
        print(f"Target is not empty: {populated_targets}")
        return 1

    for name in COPY_ORDER:
        print(f"{name}: {source_counts[name]}")

    if not args.execute:
        print("Dry run complete. Add --execute to copy the data.")
        return 0

    with source_engine.connect() as source_connection:
        with target_engine.begin() as target_connection:
            for name in COPY_ORDER:
                source_table = source_metadata.tables[name]
                target_table = target_metadata.tables[name]
                rows = list(
                    source_connection.execute(select(source_table)).mappings()
                )

                if rows:
                    target_connection.execute(
                        target_table.insert(),
                        [dict(row) for row in rows],
                    )

            for name in COPY_ORDER:
                copied = row_count(
                    target_connection,
                    target_metadata.tables[name],
                )

                if copied != source_counts[name]:
                    raise RuntimeError(
                        f"Count mismatch for {name}: "
                        f"expected {source_counts[name]}, copied {copied}"
                    )

    print("Data migration completed and verified.")
    print("Authentication sessions were intentionally not copied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
