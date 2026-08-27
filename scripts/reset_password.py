import argparse
import getpass
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database import SessionLocal
from app.services.auth_service import AuthService, normalize_username


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reset a TrainingBuddy user's password.",
    )
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    try:
        username = normalize_username(args.username)
    except ValueError as error:
        print(error)
        return 2

    password = getpass.getpass("New password (12+ characters): ")
    confirmation = getpass.getpass("Confirm new password: ")

    if password != confirmation:
        print("Passwords do not match")
        return 2

    db = SessionLocal()

    try:
        service = AuthService(db=db)
        user = next(
            (
                candidate
                for candidate in service.list_users()
                if candidate.username == username
            ),
            None,
        )

        if user is None:
            print(f"User not found: {username}")
            return 1

        service.update_user(
            user_id=user.user_id,
            password=password,
        )
    except ValueError as error:
        print(error)
        return 2
    finally:
        db.close()

    print(f"Password reset for: {username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
