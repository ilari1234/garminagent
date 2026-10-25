import json
import os

import pytest
from typer.testing import CliRunner

from garminagent import __version__
from garminagent.cli import app
from garminagent.config import Settings
from garminagent.garmin import GarminClient
from tests.garmin.fakes import TEST_API_KEY, FakeGarmin, FakeTime

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_config_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARMIN_PERSONAL_API_KEY", "secret-key")
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "secret-key" not in result.stdout
    assert json.loads(result.stdout)["garmin_personal_api_key"] == "set"


def test_db_check_fails_cleanly_without_database(monkeypatch: pytest.MonkeyPatch) -> None:
    # Nothing listens on port 1; no password needed to prove the failure path.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/none")
    result = runner.invoke(app, ["db-check"])
    assert result.exit_code == 1


def test_auth_check_without_key_fails() -> None:
    result = runner.invoke(app, ["auth-check"])
    assert result.exit_code == 1
    assert "GARMIN_PERSONAL_API_KEY is not set" in result.output


def test_auth_check_reports_success_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    garmin = FakeGarmin(FakeTime())
    garmin.json("/userprofile-service/userprofile/settings", {"fake": "profile"})

    def fake_from_settings(settings: Settings) -> GarminClient:
        return garmin.client()

    monkeypatch.setattr(GarminClient, "from_settings", fake_from_settings)
    result = runner.invoke(app, ["auth-check"])
    assert result.exit_code == 0, result.output
    assert "API key accepted" in result.output
    assert "fake" not in result.output
    assert TEST_API_KEY not in result.output


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_db_check_against_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    result = runner.invoke(app, ["db-check"])
    assert result.exit_code == 0, result.output
