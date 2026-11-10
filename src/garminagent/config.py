"""Application settings, read from environment variables and an optional .env file.

Everything that differs between local Docker and AWS is configured here, so the code
itself never changes between environments.
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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
DEFAULT_ANALYTICS_MAX_ACTIVITIES = 200
ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# Banister's published TRIMP coefficients (men, women). The value implies sex, so it is never
# shown, logged or stored outside a metrics row's input fingerprint (ADR 0013).
TRIMP_COEFFICIENTS = (1.92, 1.67)
# S3 general purpose bucket names: lowercase letters, digits, dots and hyphens, 3-63 characters.
BUCKET_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class Settings(BaseSettings):
    # Validation errors never echo the input: a mistyped ANALYTICS_TRIMP_B would show which
    # coefficient (and so which sex) was meant, and other values may be secrets.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore", hide_input_in_errors=True
    )

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
    # Fail instead of warning when a non-local database is reached without sslmode=verify-full.
    # Set on AWS, where the URLs are typed in by hand; off locally, where compose has no TLS.
    db_require_verify_full: bool = False
    # `db-bootstrap` only: the RDS master login, from the RDS-managed secret's JSON keys. Host,
    # port and database come from MIGRATION_DATABASE_URL.
    db_master_username: SecretStr | None = None
    db_master_password: SecretStr | None = None
    # Sync: history starts at SYNC_START_DATE (default: two years back); each run makes at most
    # SYNC_MAX_REQUESTS Garmin requests, so a long backfill spreads over several runs.
    sync_start_date: date | None = None
    # At least 10: each run first spends ~7 requests (token, re-fetching recent days of every
    # dataset, the newest activity page) and must have budget left to make progress.
    sync_max_requests: int = Field(default=300, ge=MIN_SYNC_REQUESTS)
    sync_refetch_days: int = Field(default=3, ge=0)
    sync_activity_refetch_days: int = Field(default=7, ge=0)
    # `garminagent schedule`: run every SCHEDULE_INTERVAL_HOURS once caught up, every
    # SCHEDULE_CATCH_UP_MINUTES while a backfill is still in progress.
    schedule_interval_hours: float = Field(default=6.0, ge=1.0)
    schedule_catch_up_minutes: int = Field(default=15, ge=5)
    data_dir: Path = Path("data")
    # Raw files: `local` under DATA_DIR/blobs, or `s3` in BLOB_S3_BUCKET. S3 takes its region and
    # credentials from the default AWS chain (the ECS task role); no AWS keys are configured here.
    blob_backend: Literal["local", "s3"] = "local"
    blob_s3_bucket: str | None = None
    # Analytics (ADR 0013): the HR load coefficient has no default; while it is unset, HR load
    # isn't computed. Each run recomputes at most ANALYTICS_MAX_ACTIVITIES activities' metrics,
    # so the first catch-up spreads over runs; `analyze --full` has no cap.
    analytics_trimp_b: float | None = Field(default=None, repr=False)
    analytics_max_activities: int = Field(default=DEFAULT_ANALYTICS_MAX_ACTIVITIES, ge=1)
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

    @field_validator("analytics_trimp_b")
    @classmethod
    def _trimp_b_is_published(cls, value: float | None) -> float | None:
        # The message names the allowed values, never the rejected one.
        if value is not None and value not in TRIMP_COEFFICIENTS:
            raise ValueError("ANALYTICS_TRIMP_B must be 1.92 or 1.67")
        return value

    @field_validator("blob_s3_bucket")
    @classmethod
    def _bucket_is_valid(cls, value: str | None) -> str | None:
        if value is not None and not BUCKET_NAME.fullmatch(value):
            raise ValueError("BLOB_S3_BUCKET must be a valid S3 bucket name")
        return value

    @model_validator(mode="after")
    def _s3_needs_a_bucket(self) -> Settings:
        if self.blob_backend == "s3" and self.blob_s3_bucket is None:
            raise ValueError("BLOB_BACKEND=s3 needs BLOB_S3_BUCKET")
        return self

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    def redacted(self) -> dict[str, str | float | int | bool | None]:
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
            "db_require_verify_full": self.db_require_verify_full,
            "db_master_username": "set" if self.db_master_username else None,
            "db_master_password": "set" if self.db_master_password else None,
            "sync_start_date": self.sync_start_date.isoformat() if self.sync_start_date else None,
            "sync_max_requests": self.sync_max_requests,
            "sync_refetch_days": self.sync_refetch_days,
            "sync_activity_refetch_days": self.sync_activity_refetch_days,
            "schedule_interval_hours": self.schedule_interval_hours,
            "schedule_catch_up_minutes": self.schedule_catch_up_minutes,
            "data_dir": str(self.data_dir),
            "blob_backend": self.blob_backend,
            "blob_s3_bucket": self.blob_s3_bucket,
            "analytics_trimp_b": "set" if self.analytics_trimp_b is not None else None,
            "analytics_max_activities": self.analytics_max_activities,
            "log_level": self.log_level,
            "log_format": self.log_format,
        }


def _hide_password(url: SecretStr) -> str:
    return make_url(url.get_secret_value()).render_as_string(hide_password=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
