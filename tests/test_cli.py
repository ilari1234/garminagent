import json
import os

import pytest
from typer.testing import CliRunner

from garminagent import __version__
from garminagent.cli import app

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
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:pw@127.0.0.1:1/none")
    result = runner.invoke(app, ["db-check"])
    assert result.exit_code == 1


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_db_check_against_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    result = runner.invoke(app, ["db-check"])
    assert result.exit_code == 0, result.output
