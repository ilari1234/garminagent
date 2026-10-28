from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from garminagent.garmin.files import parse_activity_file
from garminagent.normalize import parse_activities, parse_daily_summaries
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.models import Activity, ActivityFile, Athlete, DailySummary, RawPayload, SyncCursor
from garminagent.storage.repository import (
    SyncRange,
    ensure_athlete,
    get_cursor,
    purge_athlete,
    save_activity_file,
    save_raw_payload,
    set_cursor,
    upsert_activities,
    upsert_daily_summaries,
)
from tests.garmin.fakes import load_fixture, make_fit, make_zip

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def test_ensure_athlete_is_idempotent(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        first = ensure_athlete(session)
    with app_sessions.begin() as session:
        assert ensure_athlete(session) == first
        assert session.scalar(select(func.count()).select_from(Athlete)) == 1


def test_raw_payload_dedupes_identical_content(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        first = save_raw_payload(
            session, athlete, "daily_summaries", "2026-10-01..2026-10-02", 200, {"b": 1, "a": 2}, T0
        )
        later = T0 + timedelta(hours=1)
        # Same content, different key order: same row, refreshed timestamp.
        again = save_raw_payload(
            session, athlete, "daily_summaries", "2026-10-01..2026-10-02", 200, {"a": 2, "b": 1}, later
        )
        changed = save_raw_payload(session, athlete, "daily_summaries", "2026-10-01..2026-10-02", 200, {"a": 3}, later)
        empty = save_raw_payload(session, athlete, "menstrual_summary", "2026-10-01", 204, None, later)
    assert again == first
    assert changed != first
    with app_sessions() as session:
        row = session.get_one(RawPayload, first)
        assert row.first_fetched_at == T0
        assert row.last_fetched_at == later
        assert session.get_one(RawPayload, empty).payload is None
        assert session.scalar(select(func.count()).select_from(RawPayload)) == 3


def test_activity_file_stored_by_hash_never_by_member_name(
    app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    fit = make_fit()
    download = parse_activity_file(42, make_zip({"../../escape.fit": fit}))
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        assert save_activity_file(session, blobs, athlete, download, T0) == 2  # original + fit
        assert save_activity_file(session, blobs, athlete, download, T0) == 0  # already stored
    with app_sessions() as session:
        rows = session.scalars(select(ActivityFile).order_by(ActivityFile.kind)).all()
    assert [r.kind for r in rows] == ["fit", "original"]
    fit_row = rows[0]
    assert fit_row.member_name == "../../escape.fit"  # kept as data
    assert ".." not in fit_row.blob_key
    assert fit_row.blob_key.startswith(f"{athlete}/activities/42/fit-")
    assert blobs.get(fit_row.blob_key) == fit
    assert blobs.get(rows[1].blob_key) == download.original


def test_bare_fit_download_gets_distinct_keys(app_sessions: sessionmaker[Session], blobs: LocalBlobStore) -> None:
    download = parse_activity_file(7, make_fit())
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        assert save_activity_file(session, blobs, athlete, download, T0) == 2
    with app_sessions() as session:
        keys = sorted(session.scalars(select(ActivityFile.blob_key)).all())
    assert keys[0].endswith(".fit") and keys[0].split("/")[-1].startswith("fit-")
    assert keys[1].endswith(".fit") and keys[1].split("/")[-1].startswith("original-")


def test_upserts_insert_then_update(app_sessions: sessionmaker[Session]) -> None:
    activities = parse_activities(load_fixture("activities_search.json")).records
    days = parse_daily_summaries(load_fixture("daily_summaries.json")).records
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        raw_id = save_raw_payload(session, athlete, "activities", "page=0", 200, [], T0)
        assert upsert_activities(session, athlete, activities, raw_id) == 5
        assert upsert_daily_summaries(session, athlete, days, raw_id) == 2
        assert upsert_activities(session, athlete, []) == 0
    revised = replace(activities[0], training_load=99.0)
    with app_sessions.begin() as session:
        upsert_activities(session, athlete, [revised])
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(Activity)) == 5
        assert session.get_one(Activity, (athlete, 1000000001)).training_load == 99.0
        day = session.get_one(DailySummary, (athlete, date(2026, 10, 1)))
        assert (day.resting_hr, day.steps, day.raw_payload_id) == (47, 12000, raw_id)


def test_sync_cursor(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        assert get_cursor(session, athlete, "daily_summaries") is None
        set_cursor(session, athlete, "daily_summaries", SyncRange(date(2026, 9, 1), date(2026, 10, 1)))
        set_cursor(session, athlete, "daily_summaries", SyncRange(date(2026, 8, 1), date(2026, 10, 20)))
    with app_sessions() as session:
        assert get_cursor(session, athlete, "daily_summaries") == SyncRange(date(2026, 8, 1), date(2026, 10, 20))
        assert session.scalar(select(func.count()).select_from(SyncCursor)) == 1


def test_purge_removes_all_rows_and_blobs(app_sessions: sessionmaker[Session], blobs: LocalBlobStore) -> None:
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        raw_id = save_raw_payload(
            session, athlete, "activities", "page=0", 200, load_fixture("activities_search.json"), T0
        )
        upsert_activities(session, athlete, parse_activities(load_fixture("activities_search.json")).records, raw_id)
        upsert_daily_summaries(session, athlete, parse_daily_summaries(load_fixture("daily_summaries.json")).records)
        save_activity_file(session, blobs, athlete, parse_activity_file(42, make_zip({"a.fit": make_fit()})), T0)
        set_cursor(session, athlete, "activities", SyncRange(date(2026, 10, 1), date(2026, 10, 8)))

    result = purge_athlete(app_sessions, blobs, athlete)

    assert result.rows == {
        "raw_payload": 1,
        "activity_file": 2,
        "sync_cursor": 1,
        "activity": 5,
        "daily_summary": 2,
        "activity_sample": 0,
        "activity_lap": 0,
        "sleep_summary": 0,
        "hrv_summary": 0,
        "weight_daily": 0,
        "sync_run": 0,
    }
    assert result.blobs == 2
    with app_sessions() as session:
        for model in (Athlete, RawPayload, ActivityFile, SyncCursor, Activity, DailySummary):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert blobs.delete_prefix(str(athlete)) == 0
