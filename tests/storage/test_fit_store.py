"""Parsing stored FIT files into samples and laps (Postgres, as the app role)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent import sync
from garminagent.cli import app
from garminagent.garmin.files import ActivityFile as DownloadedFile
from garminagent.normalize import ActivityRecord
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.models import ActivityFile, ActivityLap, ActivitySample
from garminagent.storage.repository import ensure_athlete, purge_athlete, save_activity_file, upsert_activities
from garminagent.sync import SyncReport, parse_pending_fit_files
from tests.garmin.fakes import make_activity_fit, make_zip
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration

START = datetime(2026, 10, 1, 5, 0, tzinfo=UTC)


def store_activity(
    sessions: sessionmaker[Session], blobs: LocalBlobStore, activity_id: int, fits: dict[str, bytes]
) -> int:
    with sessions.begin() as session:
        athlete = ensure_athlete(session)
        upsert_activities(session, athlete, [ActivityRecord(activity_id=activity_id, start_time_utc=START)])
        save_activity_file(session, blobs, athlete, DownloadedFile(activity_id, make_zip(fits), fits))
    return athlete


def counts(sessions: sessionmaker[Session]) -> tuple[int, int]:
    with sessions() as session:
        samples = session.scalar(select(func.count()).select_from(ActivitySample)) or 0
        laps = session.scalar(select(func.count()).select_from(ActivityLap)) or 0
    return samples, laps


def statuses(sessions: sessionmaker[Session]) -> list[tuple[str | None, str | None, int | None]]:
    with sessions() as session:
        rows = session.execute(
            select(ActivityFile.member_name, ActivityFile.parse_status, ActivityFile.parser_version)
            .where(ActivityFile.kind == "fit")
            .order_by(ActivityFile.member_name)
        ).all()
    return [(r.member_name, r.parse_status, r.parser_version) for r in rows]


def test_parses_once_and_again_after_a_parser_upgrade(
    app_sessions: sessionmaker[Session], blobs: LocalBlobStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    athlete = store_activity(app_sessions, blobs, 1, {"1_ACTIVITY.fit": make_activity_fit(START, seconds=60)})

    report = SyncReport()
    parse_pending_fit_files(app_sessions, blobs, athlete, report)
    assert report.counts["fit_files"] == {"parsed": 1, "samples": 60}
    assert counts(app_sessions) == (60, 1)
    assert statuses(app_sessions) == [("1_ACTIVITY.fit", "parsed", 1)]

    again = SyncReport()
    parse_pending_fit_files(app_sessions, blobs, athlete, again)
    assert again.counts == {}  # nothing new

    monkeypatch.setattr(sync, "PARSER_VERSION", 2)
    upgraded = SyncReport()
    parse_pending_fit_files(app_sessions, blobs, athlete, upgraded)
    assert upgraded.counts["fit_files"]["parsed"] == 1
    assert counts(app_sessions) == (60, 1)  # replaced, not duplicated
    assert statuses(app_sessions) == [("1_ACTIVITY.fit", "parsed", 2)]


def test_invalid_file_is_marked_and_stores_nothing(app_sessions: sessionmaker[Session], blobs: LocalBlobStore) -> None:
    corrupt = bytearray(make_activity_fit(START, seconds=10))
    corrupt[40] ^= 0xFF
    athlete = store_activity(app_sessions, blobs, 2, {"2.fit": bytes(corrupt)})

    report = SyncReport()
    parse_pending_fit_files(app_sessions, blobs, athlete, report)

    assert report.counts["fit_files"] == {"invalid": 1}
    assert counts(app_sessions) == (0, 0)
    assert statuses(app_sessions) == [("2.fit", "invalid", 1)]


def test_second_fit_file_of_an_activity_is_skipped(app_sessions: sessionmaker[Session], blobs: LocalBlobStore) -> None:
    athlete = store_activity(
        app_sessions,
        blobs,
        3,
        {"a.fit": make_activity_fit(START, seconds=30), "b.fit": make_activity_fit(START, seconds=40)},
    )
    report = SyncReport()
    parse_pending_fit_files(app_sessions, blobs, athlete, report)
    assert statuses(app_sessions) == [("a.fit", "parsed", 1), ("b.fit", "skipped", 1)]
    assert counts(app_sessions) == (30, 1)


def test_purge_removes_samples_and_laps(app_sessions: sessionmaker[Session], blobs: LocalBlobStore) -> None:
    athlete = store_activity(app_sessions, blobs, 4, {"4.fit": make_activity_fit(START, seconds=20)})
    parse_pending_fit_files(app_sessions, blobs, athlete, SyncReport())
    result = purge_athlete(app_sessions, blobs, athlete)
    assert (result.rows["activity_sample"], result.rows["activity_lap"]) == (20, 1)
    assert counts(app_sessions) == (0, 0)


def test_cli_parse_fit_needs_no_garmin_key(
    migrated: PgDatabase,
    app_sessions: sessionmaker[Session],
    blobs: LocalBlobStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob_store = LocalBlobStore(tmp_path / "data" / "blobs")
    store_activity(app_sessions, blob_store, 5, {"5.fit": make_activity_fit(START, seconds=15)})
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    result = CliRunner().invoke(app, ["parse-fit"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"parsed": 1, "samples": 15}
