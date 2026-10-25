"""Application settings, read from environment variables and an optional .env file.

Everything that differs between local Docker and AWS is configured here, so the code
itself never changes between environments.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url

from garminagent.urls import validate_base_url

DEFAULT_DATABASE_URL = "postgresql+psycopg://garminagent:garminagent@127.0.0.1:5432/garminagent"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", env_ignore_empty=True, extra="ignore")

    garmin_personal_api_key: SecretStr | None = None
    garmin_api_key_expires: date | None = None
    garmin_base_url: str = "https://personal.connectapi.garmin.com"
    garmin_min_request_interval: float = 1.0
    garmin_max_retries: int = 4
    database_url: SecretStr = SecretStr(DEFAULT_DATABASE_URL)
    data_dir: Path = Path("data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    @field_validator("garmin_base_url")
    @classmethod
    def _base_url_is_safe(cls, value: str) -> str:
        return validate_base_url(value)

    def redacted(self) -> dict[str, str | float | int | None]:
        """Settings for display: the API key is masked and the database password hidden."""
        return {
            "garmin_personal_api_key": "set" if self.garmin_personal_api_key else None,
            "garmin_api_key_expires": self.garmin_api_key_expires.isoformat() if self.garmin_api_key_expires else None,
            "garmin_base_url": self.garmin_base_url,
            "garmin_min_request_interval": self.garmin_min_request_interval,
            "garmin_max_retries": self.garmin_max_retries,
            "database_url": make_url(self.database_url.get_secret_value()).render_as_string(hide_password=True),
            "data_dir": str(self.data_dir),
            "log_level": self.log_level,
            "log_format": self.log_format,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
