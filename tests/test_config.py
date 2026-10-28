import secrets
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import URL

from garminagent.config import DEFAULT_DATABASE_URL, Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.garmin_personal_api_key is None
    assert settings.database_url.get_secret_value() == DEFAULT_DATABASE_URL
    assert settings.data_dir == Path("data")
    assert settings.log_format == "text"


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "secret-key")
    monkeypatch.setenv("GARMIN_API_KEY_EXPIRES", "2026-11-23")
    monkeypatch.setenv("LOG_FORMAT", "json")
    settings = Settings()
    assert settings.garmin_personal_api_key is not None
    assert settings.garmin_personal_api_key.get_secret_value() == "secret-key"
    assert settings.garmin_api_key_expires == date(2026, 11, 23)
    assert settings.log_format == "json"


def test_reads_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DATA_DIR=/data\n", encoding="utf-8")
    assert Settings().data_dir == Path("/data")


def test_empty_values_fall_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GARMIN_PERSONAL_API_KEY=\nGARMIN_API_KEY_EXPIRES=\n", encoding="utf-8")
    settings = Settings()
    assert settings.garmin_personal_api_key is None
    assert settings.garmin_api_key_expires is None


def test_database_defaults_use_separate_roles() -> None:
    settings = Settings()
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://garminagent_app@")
    assert settings.migration_database_url.get_secret_value().startswith("postgresql+psycopg://garminagent@")
    assert settings.blob_dir == Path("data") / "blobs"


@pytest.mark.parametrize("role", ["garminagent_app", "a", "_x1"])
def test_valid_app_roles(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    monkeypatch.setenv("DB_APP_ROLE", role)
    assert Settings().db_app_role == role


@pytest.mark.parametrize("role", ["App", "x; DROP TABLE athlete", "1abc", "a-b", "x" * 64, "a b"])
def test_invalid_app_roles_are_rejected(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    monkeypatch.setenv("DB_APP_ROLE", role)
    with pytest.raises(ValidationError, match="DB_APP_ROLE"):
        Settings()


def test_redacted_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "secret-key")
    password = secrets.token_hex(8)  # built at runtime: no credential-shaped literals in the repo
    url = URL.create("postgresql+psycopg", username="user", password=password, host="db", port=5432, database="app")
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    shown = str(Settings().redacted())
    assert "secret-key" not in shown
    assert password not in shown
    assert "postgresql+psycopg://user:***@db:5432/app" in shown


def test_sync_budget_has_a_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNC_MAX_REQUESTS", "5")
    with pytest.raises(ValidationError, match="sync_max_requests"):
        Settings()
    monkeypatch.setenv("SYNC_MAX_REQUESTS", "10")
    assert Settings().sync_max_requests == 10
