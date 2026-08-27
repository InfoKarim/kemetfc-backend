from pathlib import Path
import secrets
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from app.database import SessionLocal, engine
from app.db_models import AuthSessionDB, UserDB
from app.services.auth_service import AuthService
from main import app


def main() -> int:
    if engine.dialect.name != "postgresql":
        print("PostgreSQL DATABASE_URL is required")
        return 2

    username = f"smoke-{secrets.token_hex(6)}"
    password = f"Smoke-{secrets.token_urlsafe(20)}"
    db = SessionLocal()
    user_id = None

    try:
        user = AuthService(db=db).create_user(
            username=username,
            password=password,
            role="admin",
        )
        user_id = user.user_id
        client = TestClient(app)

        login = client.post(
            "/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200, login.text

        identity = client.get("/auth/me")
        assert identity.status_code == 200, identity.text
        assert identity.json()["user"]["username"] == username

        readiness = client.get("/health/ready")
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()["database"] == "ok"
    finally:
        if user_id is not None:
            db.query(AuthSessionDB).filter(
                AuthSessionDB.user_id == user_id
            ).delete(synchronize_session=False)
            db.query(UserDB).filter(
                UserDB.user_id == user_id
            ).delete(synchronize_session=False)
            db.commit()
        db.close()

    print("PostgreSQL API smoke test: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
