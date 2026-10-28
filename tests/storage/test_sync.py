"""End-to-end sync against a fake Garmin account and a real Postgres (as the app role)."""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.cli import app
from garminagent.config import MIN_SYNC_REQUESTS, Settings
from garminagent.garmin import GarminClient, UnexpectedFileError
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.models import (
    Activity,
    ActivityFile,
    ActivitySample,
    DailySummary,
    HrvSummary,
    SleepSummary,
    WeightDaily,
)
from garminagent.storage.repository import SyncRange, get_cursor
from garminagent.sync import ACTIVITIES, DAILY_SUMMARIES, HRV, SLEEP, WEIGHT, Syncer, SyncReport
from tests.garmin.fakes import FakeGarmin, make_activity_fit, make_zip
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration

FIRST, TODAY = date(2025, 11, 1), date(2026, 1, 31)
DAILY_PATH = "/usersummary-service/v2/dailysummaries"
SEARCH_PATH = "/activitylist-service/activities/search/activities"
DOWNLOAD_PATTERN = r"/download-service/files/activity/(\d+)"
SLEEP_PATH = "/sleep-service/sleep/dailySleepSummariesByDate"
HRV_PATTERN = r"/hrv-service/hrv/daily/([\d-]+)/([\d-]+)"
WEIGHT_PATTERN = r"/weight-service/weight/range/([\d-]+)/([\d-]+)"


@dataclass
class FakeAccount:
    """A synthetic Garmin account: one daily summary per day and an activity every 3 days."""

    days: list[date] = field(default_factory=lambda: [FIRST + timedelta(d) for d in range((TODAY - FIRST).days + 1)])
    missing_file: int = 2_000_003
    invalid_file: int = 2_000_005

    def __post_init__(self) -> None:
        self.activities: list[dict[str, Any]] = [
            {
                "activityId": 2_000_000 + i,
                "startTimeGMT": f"{day} 05:00:00",
                "startTimeLocal": f"{day} 07:00:00",
                "activityType": {"typeKey": "running"},
                "duration": 3600.0,
                "activityTrainingLoad": 80.0 + i,
            }
            for i, day in enumerate(self.days[::3])
        ]

    def install(self, garmin: FakeGarmin) -> None:
        garmin.route("GET", DAILY_PATH, self.daily)
        garmin.route("GET", SEARCH_PATH, self.search)
        garmin.route("GET", DOWNLOAD_PATTERN, self.download)
        garmin.route("GET", SLEEP_PATH, self.sleep)
        garmin.route("GET", HRV_PATTERN, self.hrv)
        garmin.route("GET", WEIGHT_PATTERN, self.weight)

    @staticmethod
    def _range(request: httpx.Request) -> tuple[date, date]:
        return date.fromisoformat(request.url.params["startDate"]), date.fromisoformat(request.url.params["endDate"])

    @staticmethod
    def _path_range(request: httpx.Request) -> tuple[date, date]:
        start, end = request.url.path.rsplit("/", 2)[-2:]
        return date.fromisoformat(start), date.fromisoformat(end)

    def _between(self, start: date, end: date) -> list[date]:
        return [d for d in self.days if start <= d <= end]

    def sleep(self, request: httpx.Request) -> httpx.Response:
        items = [
            {"calendarDate": d.isoformat(), "sleepTimeSeconds": 27000, "sleepScores": {"overall": {"value": 80}}}
            for d in self._between(*self._range(request))
        ]
        return httpx.Response(200, json=items)

    def hrv(self, request: httpx.Request) -> httpx.Response:
        items = [
            {"calendarDate": d.isoformat(), "lastNightAvg": 60, "status": "BALANCED"}
            for d in self._between(*self._path_range(request))
        ]
        return httpx.Response(200, json={"userProfilePk": 1, "hrvSummaries": items})

    def weight(self, request: httpx.Request) -> httpx.Response:
        weigh_ins = [d for d in self._between(*self._path_range(request)) if d.weekday() == 0]  # Mondays
        items = [
            {"summaryDate": d.isoformat(), "numOfWeightEntries": 1, "latestWeight": {"weight": 72.0}} for d in weigh_ins
        ]
        return httpx.Response(200, json={"dailyWeightSummaries": items})

    def daily(self, request: httpx.Request) -> httpx.Response:
        start, end = self._range(request)
        data = [
            {"calendarDate": d.isoformat(), "heartRate": {"restingValue": 50}} for d in self.days if start <= d <= end
        ]
        return httpx.Response(200, json={"data": data})

    def search(self, request: httpx.Request) -> httpx.Response:
        start, end = self._range(request)
        offset, limit = int(request.url.params["start"]), int(request.url.params["limit"])
        hits = [a for a in self.activities if start <= date.fromisoformat(a["startTimeLocal"][:10]) <= end]
        return httpx.Response(200, json=hits[offset : offset + limit])

    def download(self, request: httpx.Request) -> httpx.Response:
        activity_id = int(request.url.path.rsplit("/", 1)[1])
        if activity_id == self.missing_file:
            return httpx.Response(404, json={})
        if activity_id == self.invalid_file:
            return httpx.Response(200, content=b"<html>not a file</html>")
        start = next(
            datetime.fromisoformat(a["startTimeGMT"]).replace(tzinfo=UTC)
            for a in self.activities
            if a["activityId"] == activity_id
        )
        return httpx.Response(200, content=make_zip({f"{activity_id}_ACTIVITY.fit": make_activity_fit(start, 30)}))


@pytest.fixture
def account(garmin: FakeGarmin) -> FakeAccount:
    fake = FakeAccount()
    fake.install(garmin)
    return fake


def run_sync(
    garmin: FakeGarmin,
    sessions: sessionmaker[Session],
    blobs: LocalBlobStore,
    *,
    start: date = FIRST,
    max_requests: int = 10_000,
) -> SyncReport:
    with garmin.client(min_interval=0, max_retries=0) as client:
        return Syncer(client, sessions, blobs, start=start, today=TODAY, max_requests=max_requests, page_size=10).run()


def count(
    sessions: sessionmaker[Session],
    model: type[Activity]
    | type[DailySummary]
    | type[ActivityFile]
    | type[ActivitySample]
    | type[SleepSummary]
    | type[HrvSummary]
    | type[WeightDaily],
) -> int:
    with sessions() as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def file_statuses(sessions: sessionmaker[Session]) -> dict[str | None, int]:
    with sessions() as session:
        rows = session.execute(select(Activity.file_status, func.count()).group_by(Activity.file_status)).all()
    return {status: n for status, n in rows}


def cursor(sessions: sessionmaker[Session], dataset: str) -> SyncRange | None:
    with sessions() as session:
        return get_cursor(session, 1, dataset)


def test_backfill_then_incremental(
    garmin: FakeGarmin, account: FakeAccount, app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    report = run_sync(garmin, app_sessions, blobs)

    assert report.complete
    assert count(app_sessions, DailySummary) == len(account.days) == 92
    assert count(app_sessions, Activity) == len(account.activities) == 31
    assert file_statuses(app_sessions) == {"stored": 29, "unavailable": 1, "invalid": 1}
    assert count(app_sessions, ActivityFile) == 29 * 2  # original zip + the FIT inside
    assert report.counts["fit_files"] == {"parsed": 29, "samples": 29 * 30}
    assert count(app_sessions, ActivitySample) == 29 * 30
    assert cursor(app_sessions, DAILY_SUMMARIES) == SyncRange(FIRST, TODAY)
    assert cursor(app_sessions, ACTIVITIES) == SyncRange(FIRST, TODAY)
    assert count(app_sessions, SleepSummary) == 92
    assert count(app_sessions, HrvSummary) == 92
    assert count(app_sessions, WeightDaily) == sum(1 for d in account.days if d.weekday() == 0)
    for dataset in (SLEEP, HRV, WEIGHT):
        assert cursor(app_sessions, dataset) == SyncRange(FIRST, TODAY)

    garmin.seen.clear()
    second = run_sync(garmin, app_sessions, blobs)

    assert second.complete
    daily_calls = garmin.requests_to(DAILY_PATH)
    assert [(c.params["startDate"], c.params["endDate"]) for c in daily_calls] == [("2026-01-29", "2026-01-31")]
    search_calls = garmin.requests_to(SEARCH_PATH)
    assert [(c.params["startDate"], c.params["endDate"]) for c in search_calls] == [("2026-01-25", "2026-01-31")]
    assert not [s for s in garmin.seen if s.path.startswith("/download-service/")]  # nothing retried
    assert count(app_sessions, Activity) == 31


def test_small_budget_resumes_across_runs_without_gaps(
    garmin: FakeGarmin, account: FakeAccount, app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    reports = [run_sync(garmin, app_sessions, blobs, max_requests=MIN_SYNC_REQUESTS)]
    while not reports[-1].complete and len(reports) < 60:
        reports.append(run_sync(garmin, app_sessions, blobs, max_requests=MIN_SYNC_REQUESTS))

    assert reports[0].stopped == "budget"
    assert reports[-1].complete
    assert all(r.requests <= MIN_SYNC_REQUESTS + 1 for r in reports)  # budget plus at most the token request
    assert count(app_sessions, DailySummary) == 92
    assert count(app_sessions, Activity) == 31
    assert file_statuses(app_sessions) == {"stored": 29, "unavailable": 1, "invalid": 1}
    assert cursor(app_sessions, ACTIVITIES) == SyncRange(FIRST, TODAY)


def test_rate_limit_stops_the_run_and_keeps_progress(
    garmin: FakeGarmin, account: FakeAccount, app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    garmin.routes = [r for r in garmin.routes if r[1].pattern != SEARCH_PATH]
    garmin.reply("GET", SEARCH_PATH, httpx.Response(429, headers={"Retry-After": "3600"}))

    report = run_sync(garmin, app_sessions, blobs)

    assert report.stopped == "rate_limited"
    assert count(app_sessions, DailySummary) == 92  # finished before the limit hit
    assert cursor(app_sessions, ACTIVITIES) is None
    assert len(garmin.requests_to(SEARCH_PATH)) == 1  # no hammering


def test_server_error_on_download_stops_and_retries_later(
    garmin: FakeGarmin, account: FakeAccount, app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    garmin.routes = [r for r in garmin.routes if r[1].pattern != DOWNLOAD_PATTERN]
    garmin.route("GET", DOWNLOAD_PATTERN, lambda request: httpx.Response(503))

    report = run_sync(garmin, app_sessions, blobs)

    assert report.stopped == "garmin_error"
    assert file_statuses(app_sessions) == {None: 31}  # not marked: tried again next run


def test_earlier_start_extends_history_backwards(
    garmin: FakeGarmin, account: FakeAccount, app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    run_sync(garmin, app_sessions, blobs, start=date(2026, 1, 1))
    assert cursor(app_sessions, DAILY_SUMMARIES) == SyncRange(date(2026, 1, 1), TODAY)
    assert count(app_sessions, DailySummary) == 31

    run_sync(garmin, app_sessions, blobs, start=FIRST)

    assert cursor(app_sessions, DAILY_SUMMARIES) == SyncRange(FIRST, TODAY)
    assert cursor(app_sessions, ACTIVITIES) == SyncRange(FIRST, TODAY)
    assert count(app_sessions, DailySummary) == 92
    assert count(app_sessions, Activity) == 31


def test_cli_sync_reports_counts_only(
    garmin: FakeGarmin,
    account: FakeAccount,
    migrated: PgDatabase,
    app_sessions: sessionmaker[Session],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_from_settings(settings: Settings) -> GarminClient:
        return garmin.client(min_interval=0, max_retries=0)

    monkeypatch.setattr(GarminClient, "from_settings", fake_from_settings)
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    result = CliRunner().invoke(app, ["sync", "--since", "2026-01-20"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.stdout)  # stdout carries only the report; logs go to stderr
    assert summary["complete"] is True
    assert summary["counts"]["daily_summaries"]["records"] == 12
    assert "activityTrainingLoad" not in result.output  # no payload contents


def _only_counts(value: object) -> bool:
    if isinstance(value, dict):
        return all(isinstance(k, str) and _only_counts(v) for k, v in cast(dict[object, object], value).items())
    return isinstance(value, int) and not isinstance(value, bool)


def test_progress_logs_carry_counts_and_dates_only(
    garmin: FakeGarmin,
    account: FakeAccount,
    app_sessions: sessionmaker[Session],
    blobs: LocalBlobStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="garminagent.sync"):
        run_sync(garmin, app_sessions, blobs)

    records = [r for r in caplog.records if r.name == "garminagent.sync"]
    text = "\n".join(r.getMessage() for r in records)
    assert "daily_summaries 2025-11-01..2026-01-31: 92 days stored (requests " in text
    assert "sleep 2026-01-02..2026-01-31: 30 days stored" in text
    assert "hrv 2026-01-24..2026-01-31: 8 days stored" in text
    assert "activities 2025-11-01..2026-01-29: 30 stored from 4 pages" in text
    assert "activity files: 31 to download" in text
    assert "activity files: 25 of 31 done this run (requests " in text
    assert "sync complete after" in text
    assert "activity 2000005: file rejected (not_zip_or_fit)" in text

    # Arguments are dates, counts or fixed wording, never payload values.
    allowed_words = {"complete", DAILY_SUMMARIES, SLEEP, HRV, WEIGHT, *UnexpectedFileError.REASONS}
    for record in records:
        for arg in record.args if isinstance(record.args, tuple) else ():
            assert isinstance(arg, date) or _only_counts(arg) or arg in allowed_words, (record.msg, arg)
    for value in ("running", "activityTrainingLoad", "restingValue", "ACTIVITY.fit", "80.0", "3600"):
        assert value not in text


def test_budget_stop_is_logged(
    garmin: FakeGarmin,
    account: FakeAccount,
    app_sessions: sessionmaker[Session],
    blobs: LocalBlobStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="garminagent.sync"):
        report = run_sync(garmin, app_sessions, blobs, max_requests=3)
    assert report.stopped == "budget"
    assert "request budget of 3 reached; run sync again to continue" in caplog.text


def test_one_refusing_endpoint_does_not_block_the_others(
    garmin: FakeGarmin, account: FakeAccount, app_sessions: sessionmaker[Session], blobs: LocalBlobStore
) -> None:
    garmin.routes = [r for r in garmin.routes if r[1].pattern != WEIGHT_PATTERN]
    garmin.route("GET", WEIGHT_PATTERN, lambda request: httpx.Response(400, json={}))

    report = run_sync(garmin, app_sessions, blobs)

    assert report.complete
    assert report.counts[WEIGHT] == {"errors": 1}
    assert cursor(app_sessions, WEIGHT) is None  # retried next run
    assert count(app_sessions, SleepSummary) == 92
    assert count(app_sessions, Activity) == 31
