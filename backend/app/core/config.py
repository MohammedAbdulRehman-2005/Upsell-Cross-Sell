"""Environment-based application configuration using Pydantic v2 Settings."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

def _resolve_project_root() -> Path:
    """Resolve the root directory containing data/ whether on host or in Docker."""
    this_file = Path(__file__).resolve()
    for candidate in (this_file.parents[3], this_file.parents[2]):
        if (candidate / "data" / "normalized").exists():
            return candidate
    return this_file.parents[3]


PROJECT_ROOT = _resolve_project_root()


class Settings(BaseSettings):
    """Centralized configuration for the Plural Upsell & Cross-Sell backend."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application settings
    app_name: str = Field(
        default="Plural Upsell & Cross-Sell Service",
        min_length=1,
        description="Human-readable service name.",
    )
    app_version: str = Field(
        default="0.2.0",
        min_length=1,
        description="Semantic version string.",
    )
    environment: Literal["development", "testing", "staging", "production"] = Field(
        default="development",
        description="Runtime environment identifier.",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode (never True in production).",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Application log level.",
    )
    api_v1_prefix: str = Field(
        default="/api/v1",
        pattern=r"^/.*",
        description="Prefix for versioned API endpoints.",
    )

    # Database connection settings
    postgres_host: str = Field(default="localhost", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = Field(default="plural_user", min_length=1)
    postgres_password: SecretStr = Field(default=SecretStr(""))
    postgres_db: str = Field(default="plural_upsell", min_length=1)
    database_url: SecretStr | None = Field(
        default=None,
        description="Optional explicit SQLAlchemy database URL override.",
    )
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=10, ge=0, le=100)
    db_echo: bool = Field(default=False)

    # CORS configuration
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"],
        description="Allowed CORS origins.",
    )

    # Dataset paths
    raw_workbook_path: Path = Field(
        default=PROJECT_ROOT / "data" / "raw" / "product_mapping_source.xlsx",
        description="Path to authoritative raw source Excel workbook.",
    )
    normalized_mappings_path: Path = Field(
        default=PROJECT_ROOT / "data" / "normalized" / "product_mappings.json",
        description="Path to Stage 1 normalized product mapping JSON.",
    )
    normalized_mappings_csv_path: Path = Field(
        default=PROJECT_ROOT / "data" / "normalized" / "product_mappings.csv",
        description="Path to Stage 1 flat product mapping CSV.",
    )
    validation_report_path: Path = Field(
        default=PROJECT_ROOT / "data" / "normalized" / "validation_report.json",
        description="Path to Stage 1 dataset validation report JSON.",
    )
    synthetic_catalogue_fixture_path: Path = Field(
        default=PROJECT_ROOT / "data" / "fixtures" / "synthetic_catalogue_metadata.json",
        description="Path to optional explicitly labelled synthetic catalogue metadata fixture.",
    )

    # Stage 5: Order delivery webhook & campaign orchestration settings
    order_webhook_auth_mode: Literal["dev_insecure_open", "shared_token_header"] = Field(
        default="dev_insecure_open",
        description=(
            "Authentication mode for inbound order-delivery webhooks. "
            "'dev_insecure_open' is only allowed in development/testing; "
            "production and staging fail closed unless 'shared_token_header' is configured."
        ),
    )
    order_webhook_shared_token: SecretStr = Field(
        default=SecretStr(""),
        description="Shared secret token verified via X-Webhook-Token header when order_webhook_auth_mode='shared_token_header'.",
    )
    allowed_delivery_event_sources: list[str] = Field(
        default_factory=lambda: [
            "order_service",
            "logistics_webhook",
            "oms",
            "manual_trigger",
        ],
        description="Authorized source identifiers for order.delivered events.",
    )
    require_consent_audit_metadata: bool | None = Field(
        default=None,
        description=(
            "Technical gate requiring non-null consent_source and consent_recorded_at on customer records. "
            "Defaults to True (enforced) in 'production' and 'staging' (and cannot be disabled there); "
            "defaults to False in 'development' and 'testing' unless explicitly set to True."
        ),
    )

    # Stage 6: WhatsApp Provider & Meta Cloud API Configuration
    whatsapp_provider: Literal["simulator", "meta_cloud_api"] = Field(
        default="simulator",
        validation_alias=AliasChoices("whatsapp_provider", "whatsapp_provider_mode"),
        description="Selected WhatsApp provider ('simulator' or 'meta_cloud_api'). Simulator mode requires explicit 'simulator' selection.",
    )
    whatsapp_access_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("whatsapp_access_token", "meta_whatsapp_access_token"),
        description="Meta WhatsApp Cloud API bearer access token (never logged).",
    )
    whatsapp_phone_number_id: str = Field(
        default="",
        validation_alias=AliasChoices("whatsapp_phone_number_id", "meta_whatsapp_phone_number_id"),
        description="Meta WhatsApp Cloud API sender phone_number_id.",
    )
    whatsapp_business_account_id: str = Field(
        default="",
        description="Optional Meta WhatsApp Business Account (WABA) ID.",
    )
    whatsapp_api_version: str = Field(
        default="v21.0",
        pattern=r"^v\d+\.\d+$",
        description="Meta Graph API version (e.g. 'v21.0').",
    )
    whatsapp_base_url: str = Field(
        default="https://graph.facebook.com",
        description="Base URL for Meta Graph API.",
    )
    whatsapp_webhook_verify_token: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("whatsapp_webhook_verify_token", "meta_whatsapp_verify_token"),
        description="Verify token for GET /api/v1/webhooks/whatsapp challenge handshake.",
    )
    whatsapp_app_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("whatsapp_app_secret", "meta_whatsapp_app_secret"),
        description="Meta App Secret used to verify X-Hub-Signature-256 on status webhooks.",
    )
    whatsapp_template_name: str = Field(
        default="plural_post_delivery_upsell_v1",
        min_length=1,
        max_length=128,
        description="Approved WhatsApp template name for post-delivery upsell recommendations.",
    )
    whatsapp_template_language: str = Field(
        default="en_US",
        min_length=2,
        max_length=16,
        description="BCP-47 language code for the WhatsApp template.",
    )
    whatsapp_http_timeout_seconds: float = Field(
        default=10.0,
        gt=0.0,
        le=60.0,
        description="Finite HTTP timeout in seconds for outbound Meta Cloud API calls.",
    )

    @property
    def meta_whatsapp_access_token(self) -> SecretStr:
        """Backward-compatible alias for whatsapp_access_token."""
        return self.whatsapp_access_token

    @property
    def meta_whatsapp_phone_number_id(self) -> str:
        """Backward-compatible alias for whatsapp_phone_number_id."""
        return self.whatsapp_phone_number_id

    @property
    def meta_whatsapp_verify_token(self) -> SecretStr:
        """Backward-compatible alias for whatsapp_webhook_verify_token."""
        return self.whatsapp_webhook_verify_token

    @property
    def meta_whatsapp_app_secret(self) -> SecretStr:
        """Backward-compatible alias for whatsapp_app_secret."""
        return self.whatsapp_app_secret

    @field_validator("whatsapp_provider", mode="before")
    @classmethod
    def normalize_whatsapp_provider(cls, value: str) -> str:
        if isinstance(value, str):
            cleaned = value.strip().lower()
            if cleaned in ("meta", "meta_cloud_api", "auto"):
                return "meta_cloud_api" if cleaned != "auto" else "simulator"
            return cleaned
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return []
            if cleaned.startswith("["):
                import json

                parsed = json.loads(cleaned)
                if not isinstance(parsed, list):
                    raise ValueError("CORS_ORIGINS JSON value must be a list of strings")
                return [str(origin).strip() for origin in parsed if str(origin).strip()]
            return [origin.strip() for origin in cleaned.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def enforce_consent_audit_metadata_by_environment(self) -> Settings:
        """Enforce consent_source and consent_recorded_at in staging/production; allow dev/test default False."""
        if self.environment in ("production", "staging"):
            if self.require_consent_audit_metadata is False:
                raise ValueError(
                    f"require_consent_audit_metadata cannot be set to False in '{self.environment}' environment."
                )
            self.require_consent_audit_metadata = True
        else:
            if self.require_consent_audit_metadata is None:
                self.require_consent_audit_metadata = False
        return self

    @property
    def sqlalchemy_database_uri(self) -> str:
        """Return the unmasked SQLAlchemy connection URI for engine creation."""
        if self.database_url is not None and self.database_url.get_secret_value().strip():
            return self.database_url.get_secret_value().strip()
        raw_password = self.postgres_password.get_secret_value()
        encoded_user = quote_plus(self.postgres_user)
        encoded_password = quote_plus(raw_password)
        auth_part = f"{encoded_user}:{encoded_password}" if raw_password else encoded_user
        return (
            f"postgresql+psycopg://{auth_part}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def masked_database_uri(self) -> str:
        """Return a safe, password-redacted database URI for logs and diagnostics."""
        if self.database_url is not None and self.database_url.get_secret_value().strip():
            uri = self.database_url.get_secret_value().strip()
            if "@" in uri and "://" in uri:
                scheme_and_creds, host_part = uri.split("@", 1)
                scheme, _, creds = scheme_and_creds.partition("://")
                user = creds.split(":", 1)[0] if ":" in creds else creds
                return f"{scheme}://{user}:***@{host_part}"
            return uri
        return (
            f"postgresql+psycopg://{self.postgres_user}:***"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
