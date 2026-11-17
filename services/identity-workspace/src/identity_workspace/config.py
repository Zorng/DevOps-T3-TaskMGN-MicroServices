"""Validated environment configuration with credential-safe startup errors."""

from pathlib import Path
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, SettingsError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(RuntimeError):
    """A startup error whose message contains setting names, never values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="IDENTITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        hide_input_in_errors=True,
        frozen=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    allowed_origins: tuple[str, ...] = ()
    database_url: SecretStr = Field(repr=False)
    google_client_id: str | None = Field(default=None, repr=False)
    task_service_key: SecretStr | None = Field(default=None, repr=False)
    notification_service_key: SecretStr | None = Field(default=None, repr=False)
    task_service_key_file: Path | None = Field(default=None, repr=False)
    notification_service_key_file: Path | None = Field(default=None, repr=False)
    google_timeout_seconds: float = Field(default=2.0, gt=0, le=5, allow_inf_nan=False)
    auth_challenge_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    auth_exchange_limit_per_minute: int = Field(default=30, ge=1, le=1000)
    db_connect_timeout_seconds: int = Field(default=2, ge=2, le=30)
    db_pool_timeout_seconds: float = Field(default=2.0, gt=0, le=30, allow_inf_nan=False)
    db_statement_timeout_ms: int = Field(default=2000, ge=1, le=30000)
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=0, ge=0, le=20)

    @field_validator("task_service_key", "notification_service_key", mode="before")
    @classmethod
    def validate_service_key(cls, value):
        if value is None or value == "":
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw, str) or re.fullmatch(r"[A-Za-z0-9_-]{43,128}", raw) is None:
            raise ValueError("Must be a high-entropy URL-safe service key of 43–128 characters.")
        return value

    @field_validator("task_service_key_file", "notification_service_key_file", mode="before")
    @classmethod
    def optional_service_key_file(cls, value):
        return None if value == "" else value

    @field_validator("google_client_id", mode="before")
    @classmethod
    def validate_google_client_id(cls, value):
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}\.apps\.googleusercontent\.com", value):
            raise ValueError("Must be a Google OAuth web client ID.")
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, origins, info: ValidationInfo):
        for origin in origins:
            try:
                parts = urlsplit(origin)
                port = parts.port
            except ValueError:
                raise ValueError("Origins must be explicit HTTP(S) origins.") from None
            if (
                parts.scheme not in {"http", "https"} or not parts.hostname
                or "*" in origin or "\\" in origin or any(character.isspace() for character in origin)
                or parts.username is not None or parts.password is not None
                or parts.path or parts.query or parts.fragment
                or origin != f"{parts.scheme}://{parts.netloc.lower()}"
                or (port is not None and not 1 <= port <= 65535)
                or (parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"})
            ):
                raise ValueError("Origins must be explicit HTTPS origins or local HTTP origins.")
        if info.data.get("environment") == "production" and any(origin.startswith("http:") for origin in origins):
            raise ValueError("Production origins must use HTTPS.")
        return tuple(dict.fromkeys(origins))

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        try:
            url = make_url(raw)
            port = url.port
        except (ArgumentError, ValueError):
            raise ValueError("Must be a valid PostgreSQL connection URL.") from None
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Must use the postgresql+psycopg driver.")
        if not all((url.host, url.database, url.username, url.password)):
            raise ValueError("Must include host, database, username, and password.")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("Database port must be between 1 and 65535.")
        if raw != raw.strip() or any(ord(character) < 32 for character in raw):
            raise ValueError("Connection URL must not contain whitespace at its edges or control characters.")
        # Keep timeouts/application identity under service control. TLS settings
        # remain configurable for deployment without admitting arbitrary libpq options.
        if set(url.query) - {"sslmode", "sslrootcert", "sslcert", "sslkey"}:
            raise ValueError("Only TLS connection options are supported in the URL.")
        if any(not isinstance(item, str) for item in url.query.values()):
            raise ValueError("Connection options must not be repeated.")
        if "sslmode" in url.query and url.query["sslmode"] not in {
            "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
        }:
            raise ValueError("Unsupported PostgreSQL TLS mode.")
        return value


def load_settings(*, env_file: str | Path | None = ".env") -> Settings:
    """Load settings without exposing raw Pydantic/dotenv errors at startup."""
    try:
        return Settings(_env_file=env_file)
    except ValidationError as error:
        fields = {
            str(item["loc"][0])
            for item in error.errors(include_input=False, include_context=False)
            if item["loc"] and item["loc"][0] in Settings.model_fields
        }
        names = ", ".join(f"IDENTITY_{name.upper()}" for name in sorted(fields))
        hint = f" Check: {names}." if names else " Check the setting names."
        message = f"Invalid Identity configuration.{hint} Values are omitted."
    except (SettingsError, OSError, UnicodeError, ValueError):
        message = "Unable to load Identity configuration. Check environment settings; values are omitted."
    # Raise outside the except block: even traceback rendering cannot expose
    # an original parsing exception that might contain a credential value.
    raise ConfigurationError(message) from None
