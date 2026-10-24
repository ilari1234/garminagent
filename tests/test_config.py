from datetime import date
from pathlib import Path

import pytest

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


def test_redacted_hides_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "secret-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:hunter2@db:5432/app")
    shown = str(Settings().redacted())
    assert "secret-key" not in shown
    assert "hunter2" not in shown
    assert "postgresql+psycopg://user:***@db:5432/app" in shown
