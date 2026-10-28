"""Operational runs on Postgres (as the app role): lock, run history, status, health check."""

import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.cli import app
from garminagent.config import Settings
from garminagent.garmin import GarminAuthError, GarminClient
from garminagent.runner import ALREADY_RUNNING, AUTH_FAILED, EXIT_ERROR, EXIT_OK, run_sync_once, status
from garminagent.storage.db import make_engine, sync_lock
from garminagent.storage.models import SyncRun
from tests.garmin.fakes import FakeGarmin
from tests.storage.conftest import PgDatabase
from tests.storage.test_sync import FakeAccount

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(migrated: PgDatabase, owner_engine: Engine, tmp_path: Path) -> Settings:
    """owner_engine truncates all tables after each test."""
    return Settings(
        garmin_personal_api_key=SecretStr("unused-in-tests"),
        database_url=SecretStr(migrated.app_url),
        data_dir=tmp_path / "data",
        sync_start_date=None,
    )


@pytest.fixture
def fake_garmin(garmin: FakeGarmin, monkeypatch: pytest.MonkeyPatch) -> FakeGarmin:
    FakeAccount().install(garmin)

    def from_settings(settings: Settings) -> GarminClient:
        return garmin.client(min_interval=0, max_retries=0)

    monkeypatch.setattr(GarminClient, "from_settings", from_settings)
    return garmin


def runs(sessions: sessionmaker[Session]) -> list[SyncRun]:
    with sessions() as session:
        return list(session.scalars(select(SyncRun).order_by(SyncRun.id)))


def test_run_is_recorded_with_counts_only(
    settings: Settings, fake_garmin: FakeGarmin, app_sessions: sessionmaker[Session]
) -> None:
    outcome = run_sync_once(settings)

    assert (outcome.stopped, outcome.exit_code) == (None, EXIT_OK)
    (run,) = runs(app_sessions)
    assert run.stopped is None
    assert run.requests == outcome.report.requests if outcome.report else False
    assert set(run.counts) >= {"daily_summaries", "activities"}
    assert all(isinstance(v, int) for counts in run.counts.values() for v in counts.values())


def test_second_run_skips_while_one_holds_the_lock(
    settings: Settings, fake_garmin: FakeGarmin, migrated: PgDatabase, app_sessions: sessionmaker[Session]
) -> None:
    engine = make_engine(migrated.app_url)
    try:
        with sync_lock(engine) as acquired:
            assert acquired
            outcome = run_sync_once(settings)
    finally:
        engine.dispose()

    assert (outcome.stopped, outcome.exit_code) == (ALREADY_RUNNING, EXIT_OK)
    assert fake_garmin.seen == []  # no Garmin requests at all
    assert runs(app_sessions) == []
    assert run_sync_once(settings).stopped is None  # lock released: next run works


def test_auth_failure_is_recorded_and_fails(
    settings: Settings, app_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    def rejected(settings: Settings) -> GarminClient:
        raise GarminAuthError("API key rejected (HTTP 401); it may be expired or deleted")

    monkeypatch.setattr(GarminClient, "from_settings", rejected)
    outcome = run_sync_once(settings)

    assert (outcome.stopped, outcome.exit_code) == (AUTH_FAILED, EXIT_ERROR)
    (run,) = runs(app_sessions)
    assert run.stopped == AUTH_FAILED


def test_status_and_health(settings: Settings, fake_garmin: FakeGarmin, app_sessions: sessionmaker[Session]) -> None:
    report, healthy = status(settings, max_age_hours=1)
    assert report["last_run"] is None
    assert not healthy  # never synced: not healthy for a health check
    assert status(settings)[1]  # without a max age, "never synced" isn't a failure

    run_sync_once(settings)
    report, healthy = status(settings, max_age_hours=1)

    assert healthy
    assert report["last_run"]["complete"] is True
    assert set(report["datasets"]) >= {"daily_summaries", "activities", "sleep", "hrv", "weight"}
    assert report["pending"] == {"activity_files": 0, "fit_files": 0}

    with app_sessions.begin() as session:  # a failed latest run makes it unhealthy
        last = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(1)).one()
        last.stopped = "garmin_error"
    assert not status(settings, max_age_hours=1)[1]


def test_status_does_not_write(settings: Settings, app_sessions: sessionmaker[Session]) -> None:
    status(settings)
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(SyncRun)) == 0


def test_cli_status_health_check_exit_codes(
    settings: Settings, fake_garmin: FakeGarmin, migrated: PgDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)
    runner = CliRunner()

    unhealthy = runner.invoke(app, ["status", "--max-age-hours", "1"])
    assert unhealthy.exit_code == 1

    run_sync_once(settings)
    healthy = runner.invoke(app, ["status", "--max-age-hours", "1"])
    assert healthy.exit_code == 0, healthy.output
    assert json.loads(healthy.stdout)["healthy"] is True
