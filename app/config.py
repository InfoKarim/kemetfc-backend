import os
from urllib.parse import parse_qs, urlsplit


DEFAULT_DATABASE_URL = "sqlite:///./cloud_platform.db"
DEFAULT_SESSION_DURATION_HOURS = 12
VALID_APP_ENVIRONMENTS = {"development", "test", "production"}
VALID_VIDEO_STORAGE_BACKENDS = {"local", "s3"}
VALID_TENANCY_MODES = {"single_academy", "multi_academy"}


def get_app_environment() -> str:
    environment = os.getenv("APP_ENV", "development").strip().lower()

    if environment not in VALID_APP_ENVIRONMENTS:
        raise ValueError(
            "APP_ENV must be development, test, or production"
        )

    return environment


def get_tenancy_mode() -> str:
    mode = os.getenv("TENANCY_MODE", "single_academy").strip().lower()

    if mode not in VALID_TENANCY_MODES:
        raise ValueError(
            "TENANCY_MODE must be single_academy or multi_academy"
        )

    return mode


def get_database_url() -> str:
    value = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL).strip()

    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")

    if value.startswith("postgresql://"):
        return (
            "postgresql+psycopg://"
            + value.removeprefix("postgresql://")
        )

    return value


def get_positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))

    if value <= 0:
        raise ValueError(f"{name} must be positive")

    return value


def get_database_pool_size() -> int:
    return get_positive_int("DB_POOL_SIZE", 5)


def get_database_max_overflow() -> int:
    value = int(os.getenv("DB_MAX_OVERFLOW", "10"))

    if value < 0:
        raise ValueError("DB_MAX_OVERFLOW cannot be negative")

    return value


def get_database_pool_timeout_seconds() -> int:
    return get_positive_int("DB_POOL_TIMEOUT_SECONDS", 30)


def get_database_pool_recycle_seconds() -> int:
    return get_positive_int("DB_POOL_RECYCLE_SECONDS", 1800)


def get_session_duration_hours() -> int:
    value = int(
        os.getenv(
            "AUTH_SESSION_DURATION_HOURS",
            str(DEFAULT_SESSION_DURATION_HOURS),
        )
    )

    if value <= 0:
        raise ValueError("AUTH_SESSION_DURATION_HOURS must be positive")

    return value


def get_auth_max_failed_attempts() -> int:
    return get_positive_int("AUTH_MAX_FAILED_ATTEMPTS", 5)


def get_auth_lockout_minutes() -> int:
    return get_positive_int("AUTH_LOCKOUT_MINUTES", 15)


def get_auth_cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_smtp_host() -> str:
    return os.getenv("SMTP_HOST", "smtp.gmail.com").strip()


def get_smtp_port() -> int:
    return get_positive_int("SMTP_PORT", 587)


def get_smtp_username() -> str:
    return os.getenv("SMTP_USERNAME", "").strip()


def get_smtp_password() -> str:
    return os.getenv("SMTP_PASSWORD", "")


def get_smtp_from_email() -> str:
    return os.getenv("SMTP_FROM_EMAIL", "").strip() or get_smtp_username()


def get_anthropic_api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def get_youtube_api_key() -> str:
    return os.getenv("YOUTUBE_API_KEY", "").strip()


def get_video_storage_backend() -> str:
    backend = os.getenv("VIDEO_STORAGE_BACKEND", "local").strip().lower()

    if backend not in VALID_VIDEO_STORAGE_BACKENDS:
        raise ValueError("VIDEO_STORAGE_BACKEND must be local or s3")

    return backend


def get_s3_bucket() -> str:
    bucket = os.getenv("S3_VIDEO_BUCKET", "").strip()

    if not bucket:
        raise ValueError("S3_VIDEO_BUCKET is required for S3 storage")

    return bucket


def get_s3_prefix() -> str:
    return os.getenv("S3_VIDEO_PREFIX", "videos").strip().strip("/")


def get_s3_region() -> str | None:
    return os.getenv("S3_REGION", "").strip() or None


def get_s3_endpoint_url() -> str | None:
    return os.getenv("S3_ENDPOINT_URL", "").strip() or None


def get_s3_presigned_url_expiry_seconds() -> int:
    return get_positive_int("S3_PRESIGNED_URL_EXPIRY_SECONDS", 300)


def get_public_site_origins() -> list[str]:
    default = "http://localhost:3100,https://kemetfc.com,https://www.kemetfc.com"
    raw = os.getenv("PUBLIC_SITE_ORIGINS", default)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def validate_production_settings(database_url: str | None = None) -> None:
    if get_app_environment() != "production":
        return

    url = database_url or get_database_url()

    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("Production requires a PostgreSQL DATABASE_URL")

    if not get_auth_cookie_secure():
        raise ValueError("Production requires AUTH_COOKIE_SECURE=true")

    if get_video_storage_backend() != "s3":
        raise ValueError("Production requires VIDEO_STORAGE_BACKEND=s3")

    get_s3_bucket()

    sslmode = parse_qs(urlsplit(url).query).get("sslmode", [None])[0]

    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ValueError(
            "Production PostgreSQL requires sslmode=require or stronger"
        )

    if "TENANCY_MODE" not in os.environ:
        raise ValueError(
            "Production requires an explicit TENANCY_MODE=single_academy"
        )

    if get_tenancy_mode() != "single_academy":
        raise ValueError(
            "multi_academy is not supported until organization-level "
            "data isolation is implemented"
        )
