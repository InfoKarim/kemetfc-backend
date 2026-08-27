import pytest

from app.config import (
    get_auth_cookie_secure,
    get_app_environment,
    get_database_max_overflow,
    get_database_pool_size,
    get_database_url,
    get_s3_presigned_url_expiry_seconds,
    get_session_duration_hours,
    get_tenancy_mode,
    get_video_storage_backend,
    validate_production_settings,
)


def test_database_url_defaults_to_local_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert get_database_url() == "sqlite:///./cloud_platform.db"


def test_database_url_uses_environment(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://user:password@db/trainingbuddy",
    )

    assert get_database_url() == (
        "postgresql+psycopg://user:password@db/trainingbuddy"
    )


def test_database_url_normalizes_postgres_alias(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://user:password@db/trainingbuddy?sslmode=require",
    )

    assert get_database_url() == (
        "postgresql+psycopg://user:password@db/"
        "trainingbuddy?sslmode=require"
    )


def test_auth_defaults_are_safe_for_local_development(monkeypatch):
    monkeypatch.delenv("AUTH_SESSION_DURATION_HOURS", raising=False)
    monkeypatch.delenv("AUTH_COOKIE_SECURE", raising=False)

    assert get_session_duration_hours() == 12
    assert get_auth_cookie_secure() is False


def test_auth_settings_use_environment(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_DURATION_HOURS", "8")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")

    assert get_session_duration_hours() == 8
    assert get_auth_cookie_secure() is True


def test_session_duration_must_be_positive(monkeypatch):
    monkeypatch.setenv("AUTH_SESSION_DURATION_HOURS", "0")

    with pytest.raises(ValueError):
        get_session_duration_hours()


def test_database_pool_settings(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "12")

    assert get_database_pool_size() == 7
    assert get_database_max_overflow() == 12


def test_production_requires_postgresql_tls_and_secure_cookie(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-videos")
    monkeypatch.setenv("TENANCY_MODE", "single_academy")

    with pytest.raises(ValueError, match="PostgreSQL"):
        validate_production_settings("sqlite:///./production.db")

    with pytest.raises(ValueError, match="sslmode"):
        validate_production_settings(
            "postgresql+psycopg://user:password@db/trainingbuddy"
        )

    validate_production_settings(
        "postgresql+psycopg://user:password@db/"
        "trainingbuddy?sslmode=require"
    )


def test_production_requires_secure_cookie(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-videos")
    monkeypatch.setenv("TENANCY_MODE", "single_academy")

    with pytest.raises(ValueError, match="AUTH_COOKIE_SECURE"):
        validate_production_settings(
            "postgresql+psycopg://user:password@db/"
            "trainingbuddy?sslmode=require"
        )


def test_app_environment_validation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "preview")

    with pytest.raises(ValueError, match="APP_ENV"):
        get_app_environment()


def test_video_storage_defaults_to_local(monkeypatch):
    monkeypatch.delenv("VIDEO_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("S3_PRESIGNED_URL_EXPIRY_SECONDS", raising=False)

    assert get_video_storage_backend() == "local"
    assert get_s3_presigned_url_expiry_seconds() == 300


def test_production_requires_private_s3_video_storage(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("TENANCY_MODE", "single_academy")
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "local")

    with pytest.raises(ValueError, match="VIDEO_STORAGE_BACKEND=s3"):
        validate_production_settings(
            "postgresql+psycopg://user:password@db/"
            "trainingbuddy?sslmode=require"
        )

    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "s3")
    monkeypatch.delenv("S3_VIDEO_BUCKET", raising=False)

    with pytest.raises(ValueError, match="S3_VIDEO_BUCKET"):
        validate_production_settings(
            "postgresql+psycopg://user:password@db/"
            "trainingbuddy?sslmode=require"
        )


def test_tenancy_mode_defaults_to_single_academy(monkeypatch):
    monkeypatch.delenv("TENANCY_MODE", raising=False)

    assert get_tenancy_mode() == "single_academy"


def test_production_requires_explicit_single_academy_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "true")
    monkeypatch.setenv("VIDEO_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_VIDEO_BUCKET", "private-videos")
    monkeypatch.delenv("TENANCY_MODE", raising=False)
    database_url = (
        "postgresql+psycopg://user:password@db/"
        "trainingbuddy?sslmode=require"
    )

    with pytest.raises(ValueError, match="explicit TENANCY_MODE"):
        validate_production_settings(database_url)

    monkeypatch.setenv("TENANCY_MODE", "multi_academy")

    with pytest.raises(ValueError, match="data isolation"):
        validate_production_settings(database_url)
