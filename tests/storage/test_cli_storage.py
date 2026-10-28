import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.cli import app
from garminagent.garmin.files import parse_activity_file
from garminagent.storage import migrate
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.models import Athlete
from garminagent.storage.repository import ensure_athlete, save_activity_file, save_raw_payload
from tests.garmin.fakes import make_fit, make_zip
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration
runner = CliRunner()


def test_db_upgrade_reports_revision(migrated: PgDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIGRATION_DATABASE_URL", migrated.owner_url)
    monkeypatch.setenv("DB_APP_ROLE", migrated.app_role)
    result = runner.invoke(app, ["db-upgrade"])
    assert result.exit_code == 0, result.output
    assert f"revision {migrate.head_revision()}" in result.output


def test_purge_data_deletes_everything(
    migrated: PgDatabase, app_sessions: sessionmaker[Session], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blob_store = LocalBlobStore(tmp_path / "data" / "blobs")
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        save_raw_payload(session, athlete, "activities", "page=0", 200, [{"activityId": 1}])
        save_activity_file(session, blob_store, athlete, parse_activity_file(1, make_zip({"1.fit": make_fit()})))
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    refused = runner.invoke(app, ["purge-data"])
    assert refused.exit_code == 2
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Athlete)) == 1

    result = runner.invoke(app, ["purge-data", "--yes"])
    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)  # logs go to stderr
    assert summary["rows"]["raw_payload"] == 1
    assert summary["blobs"] == 2
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Athlete)) == 0
    assert not (tmp_path / "data" / "blobs" / str(athlete)).exists()
