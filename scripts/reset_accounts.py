import argparse
import getpass
from pathlib import Path
import sys
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.db_models import AuthSessionDB, UserDB
from app.services.auth_service import (
    hash_password,
    normalize_username,
    utcnow,
)


def reset_accounts(
    db: Session,
    admin_username: str,
    admin_password: str,
) -> tuple[UserDB, int]:
    username = normalize_username(admin_username)
    password_hash = hash_password(admin_password)
    now = utcnow()
    users = db.query(UserDB).all()
    existing_admin = next(
        (user for user in users if user.username == username),
        None,
    )

    db.query(AuthSessionDB).delete(synchronize_session=False)

    for user in users:
        user.active = False
        user.failed_login_attempts = 0
        user.locked_until = None
        user.updated_at = now

    if existing_admin is None:
        existing_admin = UserDB(
            user_id=str(uuid4()),
            username=username,
            password_hash=password_hash,
            role="admin",
            active=True,
            feature_permissions=None,
            failed_login_attempts=0,
            locked_until=None,
            created_at=now,
            updated_at=now,
        )
        db.add(existing_admin)
    else:
        existing_admin.password_hash = password_hash
        existing_admin.role = "admin"
        existing_admin.active = True
        existing_admin.feature_permissions = None
        existing_admin.failed_login_attempts = 0
        existing_admin.locked_until = None
        existing_admin.updated_at = now

    db.commit()
    db.refresh(existing_admin)
    return existing_admin, len(users)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Disable every TrainingBuddy account, revoke all sessions, and "
            "activate one fresh administrator without deleting academy data."
        ),
    )
    parser.add_argument("--username", default="karim")
    args = parser.parse_args()

    password = getpass.getpass("New admin password (12+ characters): ")
    confirmation = getpass.getpass("Confirm new admin password: ")

    if password != confirmation:
        print("Passwords do not match")
        return 2

    confirmation_text = input(
        "Type RESET to disable all other accounts and sessions: "
    ).strip()

    if confirmation_text != "RESET":
        print("Account reset cancelled")
        return 2

    db = SessionLocal()

    try:
        admin, previous_count = reset_accounts(
            db=db,
            admin_username=args.username,
            admin_password=password,
        )
    except ValueError as error:
        db.rollback()
        print(error)
        return 1
    finally:
        db.close()

    print(f"Disabled {previous_count} existing account(s).")
    print("Revoked all existing login sessions.")
    print(f"Active administrator: {admin.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
