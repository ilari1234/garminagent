"""`daily_recovery` through `analyze`, against a real Postgres as the app role: what is stored,
that an unchanged history costs no writes, that changes rewrite only what they touch, and that
only counts leave the database. Synthetic values only."""

import json
import logging
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.analytics import analyze
from garminagent.cli import app
from garminagent.normalize import DailySummaryRecord, HrvRecord
from garminagent.storage.models import DailyRecovery, HrvSummary
from garminagent.storage.repository import ensure_athlete, upsert_daily_summaries, upsert_hrv
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration

D0 = date(2026, 1, 5)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def setup(sessions: sessionmaker[Session], days: int = 70) -> int:
    """HRV alternating 40/60 ms and resting HR alternating 48/52 bpm on days 0..days-1, then
    the last 7 days at 40 ms and 52 bpm: HRV below, resting HR above from day 66."""
    with sessions.begin() as session:
        athlete = ensure_athlete(session)
        upsert_hrv(
            session,
            athlete,
            [
                HrvRecord(calendar_date=day(n), last_night_avg=40 if n % 2 == 0 or n >= days - 7 else 60)
                for n in range(days)
            ],
        )
        upsert_daily_summaries(
            session,
            athlete,
            [
                DailySummaryRecord(calendar_date=day(n), resting_hr=48 if n % 2 == 0 and n < days - 7 else 52)
                for n in range(days)
            ],
        )
    return athlete


def rows(sessions: sessionmaker[Session]) -> list[DailyRecovery]:
    with sessions() as session:
        found = list(session.scalars(select(DailyRecovery).order_by(DailyRecovery.calendar_date)))
        session.expunge_all()
        return found


def test_analyze_stores_a_row_per_day_with_trends(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions, days=67)  # last 7 days: 60-66

    result = analyze(app_sessions, athlete)

    assert "errors" not in result.counts
    assert (result.counts["daily_recovery_days"], result.counts["daily_recovery_inserted"]) == (67, 67)
    # Recent means from day 3 (4 nights); baselines from day 36 (30 nights, days 0-29).
    assert result.counts["daily_recovery_hrv_days"] == result.counts["daily_recovery_rhr_days"] == 64
    assert result.counts["daily_recovery_hrv_flagged_days"] == result.counts["daily_recovery_rhr_flagged_days"] == 31
    stored = rows(app_sessions)
    assert [r.calendar_date for r in stored] == [day(n) for n in range(67)]
    last = stored[-1]
    assert (last.hrv_days, last.hrv_baseline_days, last.hrv_trend) == (7, 60, "below")
    assert (last.rhr_days, last.rhr_baseline_days, last.rhr_trend) == (7, 60, "above")
    assert last.rhr_mean == pytest.approx(52.0)
    assert last.rhr_baseline_mean == pytest.approx(50.0)
    assert stored[35].rhr_trend is None and stored[36].rhr_trend is not None
    assert all(r.algo_version == 1 for r in stored)


def test_an_unchanged_history_costs_no_writes(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)
    analyze(app_sessions, athlete)
    with app_sessions() as session:
        before = dict(session.execute(text("SELECT calendar_date, updated_at FROM daily_recovery")).all())

    again = analyze(app_sessions, athlete)

    assert (again.counts["daily_recovery_inserted"], again.counts["daily_recovery_updated"]) == (0, 0)
    assert again.counts["daily_recovery_deleted"] == 0
    with app_sessions() as session:
        after = dict(session.execute(text("SELECT calendar_date, updated_at FROM daily_recovery")).all())
    assert after == before


def test_a_changed_night_rewrites_only_the_days_that_see_it(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions, days=200)
    analyze(app_sessions, athlete)

    with app_sessions.begin() as session:
        upsert_hrv(session, athlete, [HrvRecord(calendar_date=day(100), last_night_avg=55)])
    result = analyze(app_sessions, athlete)

    # Day 100 is in the recent window of days 100-106 and the baseline of days 107-166.
    assert (result.counts["daily_recovery_inserted"], result.counts["daily_recovery_updated"]) == (0, 67)


def test_the_series_follows_the_inputs(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)
    analyze(app_sessions, athlete)
    with app_sessions.begin() as session:
        session.execute(delete(HrvSummary).where(HrvSummary.calendar_date == day(69)))
        upsert_daily_summaries(session, athlete, [DailySummaryRecord(calendar_date=day(69), resting_hr=None)])

    result = analyze(app_sessions, athlete)

    assert result.counts["daily_recovery_deleted"] == 1
    assert rows(app_sessions)[-1].calendar_date == day(68)


COUNTS = (
    "INSERT INTO daily_recovery (athlete_id, calendar_date, algo_version,"
    " hrv_days, hrv_baseline_days, rhr_days, rhr_baseline_days"
)
# Per measure: a trend with no inputs, and a trend with all its inputs.
TREND_ROWS = {
    "hrv": (
        COUNTS + ", hrv_trend) VALUES (:a, '2026-03-01', 1, 0, 0, 0, 0, :trend)",
        COUNTS + ", hrv_ln_mean, hrv_baseline_ln_mean, hrv_baseline_ln_sd, hrv_trend)"
        " VALUES (:a, '2026-03-01', 1, 7, 60, 7, 60, 1, 1, 1, :trend)",
    ),
    "rhr": (
        COUNTS + ", rhr_trend) VALUES (:a, '2026-03-01', 1, 0, 0, 0, 0, :trend)",
        COUNTS + ", rhr_mean, rhr_baseline_mean, rhr_baseline_sd, rhr_trend)"
        " VALUES (:a, '2026-03-01', 1, 7, 60, 7, 60, 1, 1, 1, :trend)",
    ),
}


@pytest.mark.parametrize("measure", TREND_ROWS)
def test_a_trend_without_its_inputs_or_with_free_text_is_refused(
    app_sessions: sessionmaker[Session], measure: str
) -> None:
    """Both measures' CHECK constraints, in the database: migrate.check doesn't compare them."""
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
    bare, full = TREND_ROWS[measure]
    with app_sessions() as session, pytest.raises(IntegrityError, match=f'"ck_daily_recovery_{measure}_trend_inputs"'):
        session.execute(text(bare), {"a": athlete, "trend": "below"})
    with app_sessions() as session, pytest.raises(IntegrityError, match=f'"ck_daily_recovery_{measure}_trend"'):
        session.execute(text(full), {"a": athlete, "trend": "freetext"})
    with app_sessions.begin() as session:
        session.execute(text(full), {"a": athlete, "trend": "within"})


@pytest.fixture
def cli_env(migrated: PgDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)


def test_cli_analyze_prints_counts_only(
    cli_env: None, app_sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    setup(app_sessions)

    with caplog.at_level(logging.DEBUG):
        result = CliRunner().invoke(app, ["analyze"])

    assert result.exit_code == 0, result.output
    counts = json.loads(result.stdout)["counts"]
    assert counts["daily_recovery_days"] == 70
    assert all(isinstance(v, int) for v in counts.values())
    shown = result.output + caplog.text
    for r in rows(app_sessions):
        for value in (r.hrv_ln_mean, r.hrv_baseline_ln_sd, r.rhr_baseline_sd):
            if value:
                assert f"{value:.2f}" not in shown and f"{value:.3f}" not in shown
    for trend in ("below", "above", "within"):
        assert trend not in shown
