import argparse
import getpass
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.services.auth_service import AuthService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create the initial TrainingBuddy administrator.",
    )
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = os.getenv("TRAININGBUDDY_ADMIN_PASSWORD")

    if password is None:
        password = getpass.getpass("Admin password (12+ characters): ")
        confirmation = getpass.getpass("Confirm admin password: ")

        if password != confirmation:
            print("Passwords do not match")
            return 2

    db = SessionLocal()

    try:
        user = AuthService(db=db).create_user(
            username=args.username,
            password=password,
            role="admin",
        )
    except ValueError as error:
        print(error)
        return 1
    finally:
        db.close()

    print(f"Created administrator: {user.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
