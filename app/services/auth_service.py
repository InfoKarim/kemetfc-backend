import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import (
    get_auth_lockout_minutes,
    get_auth_max_failed_attempts,
    get_session_duration_hours,
)
from app.db_models import AuthSessionDB, PasswordResetCodeDB, UserDB


VALID_ROLES = {"admin", "coach", "reviewer", "guardian"}
RESET_CODE_TTL_MINUTES = 15
RESET_CODE_MAX_ATTEMPTS = 5
FEATURE_PERMISSIONS = {
    "dashboard",
    "players",
    "teams",
    "assessments",
    "training",
    "videos",
    "matches",
    "reports",
    "calendar",
    "messaging",
}
DEFAULT_ROLE_FEATURES = {
    "admin": FEATURE_PERMISSIONS,
    "coach": FEATURE_PERMISSIONS,
    "reviewer": {
        "dashboard",
        "players",
        "assessments",
        "videos",
        "reports",
        "messaging",
    },
    "guardian": {"messaging"},
}
USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{3,64}")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
DUMMY_PASSWORD_HASH = (
    f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
    "00112233445566778899aabbccddeeff$"
    + hashlib.scrypt(
        b"invalid-password",
        salt=bytes.fromhex("00112233445566778899aabbccddeeff"),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    ).hex()
)


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()

    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Username must contain 3-64 lowercase letters, numbers, dots, "
            "underscores, or hyphens"
        )

    return normalized


def normalize_feature_permissions(
    permissions: list[str] | None,
) -> list[str] | None:
    if permissions is None:
        return None

    normalized = sorted(set(permissions))
    invalid = set(normalized) - FEATURE_PERMISSIONS

    if invalid:
        raise ValueError(
            f"Invalid feature permission: {sorted(invalid)[0]}"
        )

    return normalized


def effective_feature_permissions(user: UserDB) -> list[str]:
    if user.role == "admin":
        return sorted(FEATURE_PERMISSIONS)

    if user.feature_permissions is not None:
        return normalize_feature_permissions(user.feature_permissions) or []

    return sorted(DEFAULT_ROLE_FEATURES.get(user.role, set()))


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}$"
        f"{salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)

        if algorithm != "scrypt":
            return False

        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (TypeError, ValueError):
        return False


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def generate_reset_code() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


@dataclass(frozen=True)
class CreatedSession:
    token: str
    csrf_token: str
    expires_at: datetime


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    def create_user(
        self,
        username: str,
        password: str,
        role: str,
        feature_permissions: list[str] | None = None,
        email: str | None = None,
    ) -> UserDB:
        normalized = normalize_username(username)

        if role not in VALID_ROLES:
            raise ValueError("Invalid user role")

        permissions = normalize_feature_permissions(feature_permissions)

        now = utcnow()
        user = UserDB(
            user_id=str(uuid4()),
            username=normalized,
            password_hash=hash_password(password),
            email=email.strip() if email else None,
            role=role,
            active=True,
            feature_permissions=permissions,
            failed_login_attempts=0,
            locked_until=None,
            created_at=now,
            updated_at=now,
        )
        self.db.add(user)

        try:
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise ValueError("Username already exists") from error

        self.db.refresh(user)
        return user

    def list_users(self) -> list[UserDB]:
        return self.db.query(UserDB).order_by(UserDB.username).all()

    def authenticate(self, username: str, password: str) -> UserDB | None:
        try:
            normalized = normalize_username(username)
        except ValueError:
            return None

        user = (
            self.db.query(UserDB)
            .filter(UserDB.username == normalized)
            .first()
        )

        password_hash = (
            user.password_hash if user is not None else DUMMY_PASSWORD_HASH
        )
        password_valid = verify_password(password, password_hash)

        if user is None:
            return None

        now = utcnow()
        is_locked = user.locked_until is not None and user.locked_until > now

        if not user.active or is_locked:
            return None

        if not password_valid:
            user.failed_login_attempts += 1

            if user.failed_login_attempts >= get_auth_max_failed_attempts():
                user.locked_until = now + timedelta(
                    minutes=get_auth_lockout_minutes()
                )

            self.db.commit()
            return None

        if user.failed_login_attempts or user.locked_until is not None:
            user.failed_login_attempts = 0
            user.locked_until = None
            self.db.commit()

        return user

    def create_session(self, user: UserDB) -> CreatedSession:
        now = utcnow()
        expires_at = now + timedelta(hours=get_session_duration_hours())
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session = AuthSessionDB(
            session_id=str(uuid4()),
            user_id=user.user_id,
            token_hash=hash_session_token(token),
            csrf_token=csrf_token,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
        )
        self.db.add(session)
        self.db.commit()
        return CreatedSession(
            token=token,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def get_session_user(
        self,
        token: str,
    ) -> tuple[UserDB, AuthSessionDB] | None:
        session = (
            self.db.query(AuthSessionDB)
            .filter(AuthSessionDB.token_hash == hash_session_token(token))
            .first()
        )
        now = utcnow()

        if session is None:
            return None

        if session.expires_at <= now:
            self.db.delete(session)
            self.db.commit()
            return None

        user = self.db.get(UserDB, session.user_id)

        if user is None or not user.active:
            self.db.delete(session)
            self.db.commit()
            return None

        if now - session.last_seen_at >= timedelta(minutes=5):
            session.last_seen_at = now
            self.db.commit()

        return user, session

    def delete_session(self, token: str) -> bool:
        session = (
            self.db.query(AuthSessionDB)
            .filter(AuthSessionDB.token_hash == hash_session_token(token))
            .first()
        )

        if session is None:
            return False

        self.db.delete(session)
        self.db.commit()
        return True

    def create_password_reset_code(self, user_id: str) -> str:
        self.db.query(PasswordResetCodeDB).filter(
            PasswordResetCodeDB.user_id == user_id
        ).delete(synchronize_session=False)

        code = generate_reset_code()
        now = utcnow()
        self.db.add(PasswordResetCodeDB(
            reset_id=str(uuid4()),
            user_id=user_id,
            code_hash=hash_reset_code(code),
            attempts=0,
            created_at=now,
            expires_at=now + timedelta(minutes=RESET_CODE_TTL_MINUTES),
        ))
        self.db.commit()
        return code

    def consume_password_reset_code(
        self,
        user_id: str,
        code: str,
    ) -> bool:
        reset_code = (
            self.db.query(PasswordResetCodeDB)
            .filter(PasswordResetCodeDB.user_id == user_id)
            .first()
        )

        if reset_code is None:
            return False

        if reset_code.expires_at <= utcnow():
            self.db.delete(reset_code)
            self.db.commit()
            return False

        if reset_code.attempts >= RESET_CODE_MAX_ATTEMPTS:
            self.db.delete(reset_code)
            self.db.commit()
            return False

        if not hmac.compare_digest(
            hash_reset_code(code), reset_code.code_hash
        ):
            reset_code.attempts += 1
            self.db.commit()
            return False

        self.db.delete(reset_code)
        self.db.commit()
        return True

    def update_user(
        self,
        user_id: str,
        role: str | None = None,
        active: bool | None = None,
        password: str | None = None,
        feature_permissions: list[str] | None = None,
        email: str | None = None,
    ) -> UserDB | None:
        user = self.db.get(UserDB, user_id)

        if user is None:
            return None

        removes_active_admin = (
            user.role == "admin"
            and user.active
            and (
                (role is not None and role != "admin")
                or active is False
            )
        )

        if removes_active_admin:
            active_admins = (
                self.db.query(UserDB)
                .filter(UserDB.role == "admin", UserDB.active.is_(True))
                .count()
            )

            if active_admins <= 1:
                raise ValueError("Cannot remove the last active administrator")

        if role is not None:
            if role not in VALID_ROLES:
                raise ValueError("Invalid user role")
            user.role = role

        if active is not None:
            user.active = active

        if password is not None:
            user.password_hash = hash_password(password)
            user.failed_login_attempts = 0
            user.locked_until = None

        if email is not None:
            user.email = email.strip() or None

        if feature_permissions is not None:
            user.feature_permissions = normalize_feature_permissions(
                feature_permissions
            )

        user.updated_at = utcnow()

        if active is False or password is not None:
            self.db.query(AuthSessionDB).filter(
                AuthSessionDB.user_id == user.user_id
            ).delete(synchronize_session=False)

        self.db.commit()
        self.db.refresh(user)
        return user

    def delete_user(self, user_id: str) -> bool:
        user = self.db.get(UserDB, user_id)

        if user is None:
            return False

        if user.role == "admin" and user.active:
            active_admins = (
                self.db.query(UserDB)
                .filter(UserDB.role == "admin", UserDB.active.is_(True))
                .count()
            )

            if active_admins <= 1:
                raise ValueError("Cannot delete the last active administrator")

        self.db.query(AuthSessionDB).filter(
            AuthSessionDB.user_id == user.user_id
        ).delete(synchronize_session=False)
        self.db.delete(user)

        try:
            self.db.commit()
        except IntegrityError as error:
            self.db.rollback()
            raise ValueError(
                "Account has protected activity records and cannot be deleted; "
                "deactivate it instead"
            ) from error

        return True
