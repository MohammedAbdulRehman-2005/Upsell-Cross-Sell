"""Tests for environment configuration, secret masking, and validation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.logging import configure_logging


def test_settings_defaults_and_secret_masking() -> None:
    settings = Settings(
        postgres_user="app_user",
        postgres_password="super_secret_password_123",
        postgres_host="db.internal",
        postgres_port=5432,
        postgres_db="upsell_prod",
        meta_whatsapp_access_token="EAABZ_secret_token",
    )
    # SecretStr prevents accidental leakage in repr/str
    assert "super_secret_password_123" not in repr(settings.postgres_password)
    assert "EAABZ_secret_token" not in repr(settings.meta_whatsapp_access_token)
    assert "super_secret_password_123" not in settings.masked_database_uri
    assert settings.masked_database_uri == "postgresql+psycopg://app_user:***@db.internal:5432/upsell_prod"
    assert "super_secret_password_123" in settings.sqlalchemy_database_uri


def test_settings_explicit_database_url_masking() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://custom_user:top_secret@pg-host:5433/custom_db"
    )
    assert settings.sqlalchemy_database_uri == "postgresql+psycopg://custom_user:top_secret@pg-host:5433/custom_db"
    assert settings.masked_database_uri == "postgresql+psycopg://custom_user:***@pg-host:5433/custom_db"


def test_settings_cors_origins_parsing() -> None:
    from_csv = Settings(cors_origins="http://localhost:3000, https://app.example.com")
    assert from_csv.cors_origins == ["http://localhost:3000", "https://app.example.com"]

    from_json = Settings(cors_origins='["http://localhost:5173","http://localhost:8000"]')
    assert from_json.cors_origins == ["http://localhost:5173", "http://localhost:8000"]


def test_settings_invalid_environment_and_port_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="invalid_env")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        Settings(postgres_port=70000)

    with pytest.raises(ValidationError):
        Settings(log_level="VERBOSE")  # type: ignore[arg-type]


def test_configure_logging_sets_expected_level() -> None:
    settings = Settings(log_level="debug")
    logger = configure_logging(settings)
    assert settings.log_level == "DEBUG"
    assert logger.level == 10
