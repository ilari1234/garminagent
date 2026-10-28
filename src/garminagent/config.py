"""Application settings, read from environment variables and an optional .env file.

Everything that differs between local Docker and AWS is configured here, so the code
itself never changes between environments.
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

from garminagent.urls import validate_base_url

# Local defaults point at the compose database: the app as the least-privilege role, migrations
# as the schema owner. They carry no password on purpose; set DATABASE_URL and
# MIGRATION_DATABASE_URL (with passwords) in .env, or as secrets on AWS.
DEFAULT_DATABASE_URL = "postgresql+psycopg://garminagent_app@127.0.0.1:5432/garminagent"
DEFAULT_MIGRATION_DATABASE_URL = "postgresql+psycopg://garminagent@127.0.0.1:5432/garminagent"
# Postgres role names are interpolated into GRANT statements, so only plain identifiers.
MIN_SYNC_REQUESTS = 10
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")

    garmin_personal_api_key: SecretStr | None = None
    garmin_api_key_expires: date | None = None
    garmin_base_url: str = "https://personal.connectapi.garmin.com"
    garmin_min_request_interval: float = 1.0
    garmin_max_retries: int = 4
    # The app connects with a least-privilege role (data access only); migrations run as the
    # schema owner.
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    migration_database_url: SecretStr = SecretStr(DEFAULT_MIGRATION_DATABASE_URL)
    db_app_role: str = "garminagent_app"
    # Sync: history starts at SYNC_START_DATE (default: two years back); each run makes at most
    # SYNC_MAX_REQUESTS Garmin requests, so a long backfill spreads over several runs.
    sync_start_date: date | None = None
    # At least 10: each run first spends ~7 requests (token, re-fetching recent days of every
    # dataset, the newest activity page) and must have budget left to make progress.
    sync_max_requests: int = Field(default=300, ge=MIN_SYNC_REQUESTS)
    sync_refetch_days: int = Field(default=3, ge=0)
    sync_activity_refetch_days: int = Field(default=7, ge=0)
    data_dir: Path = Path("data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    @field_validator("garmin_base_url")
    @classmethod
    def _base_url_is_safe(cls, value: str) -> str:
        return validate_base_url(value)

    @field_validator("db_app_role")
    @classmethod
    def _role_is_identifier(cls, value: str) -> str:
        if not ROLE_NAME.fullmatch(value):
            raise ValueError("DB_APP_ROLE must be a lowercase identifier (letters, digits, underscore)")
        return value

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    def redacted(self) -> dict[str, str | float | int | None]:
        """Settings for display: the API key is masked and database passwords hidden."""
        return {
            "garmin_personal_api_key": "set" if self.garmin_personal_api_key else None,
            "garmin_api_key_expires": self.garmin_api_key_expires.isoformat() if self.garmin_api_key_expires else None,
            "garmin_base_url": self.garmin_base_url,
            "garmin_min_request_interval": self.garmin_min_request_interval,
            "garmin_max_retries": self.garmin_max_retries,
            "database_url": _hide_password(self.database_url),
            "migration_database_url": _hide_password(self.migration_database_url),
            "db_app_role": self.db_app_role,
            "sync_start_date": self.sync_start_date.isoformat() if self.sync_start_date else None,
            "sync_max_requests": self.sync_max_requests,
            "sync_refetch_days": self.sync_refetch_days,
            "sync_activity_refetch_days": self.sync_activity_refetch_days,
            "data_dir": str(self.data_dir),
            "log_level": self.log_level,
            "log_format": self.log_format,
        }


def _hide_password(url: SecretStr) -> str:
    return make_url(url.get_secret_value()).render_as_string(hide_password=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
