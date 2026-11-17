"""`daily_load` and `daily_load_weekly` through `analyze`, against a real Postgres as the app role:
what is stored, that an unchanged history costs no writes, that changes rewrite only what they
touch, and that only counts leave the database. Synthetic values only."""

import json
import logging
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.analytics import AnalyticsOptions, analyze
from garminagent.cli import app
from garminagent.storage.models import Activity, DailyLoad
from garminagent.storage.repository import save_hr_zones
from tests.storage.conftest import PgDatabase
from tests.storage.test_activity_metrics import D0, ON, ZONES, B, add_activity, add_resting_days

pytestmark = pytest.mark.integration


def day(n: int) -> date:
    return D0 + timedelta(days=n)


def setup(sessions: sessionmaker[Session], *, last_summary: int = 11) -> int:
    """Runs at LTHR for an hour (HR load 100) on days 7 and 9, a ride on day 9; resting HR and
    daily summaries from day 0 to `last_summary`; zones seen on day 0."""
    athlete = add_activity(sessions, 1, day(7))
    add_activity(sessions, 2, day(9))
    add_activity(sessions, 3, day(9), "cycling")
    add_resting_days(sessions, athlete, D0, last_summary + 1)
    with sessions.begin() as session:
        save_hr_zones(session, athlete, [ZONES], None, D0)
    return athlete


def rows(sessions: sessionmaker[Session], group: str = "all") -> list[DailyLoad]:
    with sessions() as session:
        found = list(
            session.scalars(select(DailyLoad).where(DailyLoad.sport_group == group).order_by(DailyLoad.calendar_date))
        )
        session.expunge_all()
        return found


def test_analyze_stores_the_series_for_every_day_and_trained_group(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)

    result = analyze(app_sessions, athlete, ON)

    assert "errors" not in result.counts
    # Days 7..11: from the first activity to the last daily summary; all, run and ride.
    assert (result.counts["daily_load_days"], result.counts["daily_load_rows"]) == (5, 15)
    assert (result.counts["daily_load_inserted"], result.counts["daily_load_updated"]) == (15, 0)
    assert result.counts["daily_load_reliable_days"] == 0
    total = rows(app_sessions)
    assert [r.calendar_date for r in total] == [day(n) for n in range(7, 12)]
    assert [r.sessions for r in total] == [1, 0, 2, 0, 0]
    assert total[0].load == pytest.approx(100.0)
    assert total[0].fitness == pytest.approx(100 / 42)
    assert total[0].fatigue == pytest.approx(100 / 7)
    assert total[0].form == 0.0
    assert total[1].form == pytest.approx(total[0].fitness - total[0].fatigue)
    assert all(r.ramp is None for r in total)  # fewer than 8 days of history
    assert [r.history_days for r in total] == [1, 2, 3, 4, 5]
    run, ride = rows(app_sessions, "run"), rows(app_sessions, "ride")
    assert [r.calendar_date for r in ride] == [r.calendar_date for r in total]
    assert ride[0].load == 0.0 and ride[2].load == pytest.approx(100.0)
    assert run[2].load + ride[2].load == pytest.approx(total[2].load)
    assert rows(app_sessions, "ski") == []


def test_an_unchanged_history_costs_no_writes(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)
    analyze(app_sessions, athlete, ON)
    with app_sessions() as session:
        before = dict(session.execute(text("SELECT calendar_date || sport_group, updated_at FROM daily_load")).all())

    again = analyze(app_sessions, athlete, ON)

    assert (again.counts["daily_load_inserted"], again.counts["daily_load_updated"]) == (0, 0)
    assert again.counts["daily_load_deleted"] == 0
    with app_sessions() as session:
        after = dict(session.execute(text("SELECT calendar_date || sport_group, updated_at FROM daily_load")).all())
    assert after == before


def test_a_new_activity_rewrites_only_its_day_and_later(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)
    analyze(app_sessions, athlete, ON)

    add_activity(app_sessions, 4, day(10))
    result = analyze(app_sessions, athlete, ON)

    # Days 10 and 11 change for all and run; ride's series and days 7-9 don't.
    assert (result.counts["daily_load_inserted"], result.counts["daily_load_updated"]) == (0, 4)


def test_the_series_follows_the_first_activity_and_the_latest_data(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)
    analyze(app_sessions, athlete, ON)
    with app_sessions.begin() as session:
        session.execute(delete(Activity).where(Activity.activity_id == 1))  # metrics cascade

    result = analyze(app_sessions, athlete, ON)

    # Days 7 and 8 go for all, run and ride; every later row changes (no day-7 load any more).
    assert result.counts["daily_load_deleted"] == 6
    assert result.counts["daily_load_days"] == 3
    assert rows(app_sessions)[0].calendar_date == day(9)
    add_resting_days(app_sessions, athlete, day(12), 3)
    assert analyze(app_sessions, athlete, ON).counts["daily_load_inserted"] == 9


def test_a_long_history_crosses_the_write_batch_and_then_stays_put(app_sessions: sessionmaker[Session]) -> None:
    # Three activities with different loads on one day (order-dependent if summed naively), three
    # groups plus `all` over 300 days of daily summaries: 1200 rows, above WRITE_BATCH.
    athlete = add_activity(app_sessions, 11, D0 + timedelta(days=7), seconds=600)
    add_activity(app_sessions, 12, D0 + timedelta(days=7), "cycling", seconds=770)
    add_activity(app_sessions, 13, D0 + timedelta(days=7), "strength_training", seconds=910)
    add_resting_days(app_sessions, athlete, D0, 307)
    with app_sessions.begin() as session:
        save_hr_zones(session, athlete, [ZONES], None, D0)

    first = analyze(app_sessions, athlete, ON)
    again = analyze(app_sessions, athlete, ON)

    assert (first.counts["daily_load_rows"], first.counts["daily_load_inserted"]) == (1200, 1200)
    assert first.counts["daily_load_reliable_days"] == 300 - 125
    assert (again.counts["daily_load_inserted"], again.counts["daily_load_updated"]) == (0, 0)
    with app_sessions.begin() as session:
        session.execute(delete(Activity).where(Activity.activity_id.in_((11, 12, 13))))
    assert analyze(app_sessions, athlete, ON).counts["daily_load_deleted"] == 1200


def test_pending_and_unloaded_activities_add_nothing_and_are_counted(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions)
    add_activity(app_sessions, 5, day(8), hr=None)  # no HR, no power: load "none"

    capped = analyze(app_sessions, athlete, AnalyticsOptions(trimp_b=B, max_activities=2))

    # Newest first: activities 3 and 2 (day 9) get metrics; 5 (day 8) and 1 (day 7) wait.
    assert capped.counts["daily_load_activities_pending"] == capped.counts["metrics_pending"] == 2
    full = analyze(app_sessions, athlete, ON)
    assert full.counts["daily_load_activities_pending"] == 0
    assert full.counts["daily_load_activities_unloaded"] == 1
    day8 = rows(app_sessions)[1]
    assert (day8.sessions, day8.load, day8.activities_unloaded) == (1, 0.0, 1)


def test_weekly_view_sums_the_week_and_takes_its_last_day(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup(app_sessions, last_summary=20)
    analyze(app_sessions, athlete, ON)
    with app_sessions() as session:
        weeks = session.execute(
            text("SELECT * FROM daily_load_weekly WHERE sport_group = 'all' ORDER BY week_start")
        ).all()
    daily = {r.calendar_date: r for r in rows(app_sessions)}
    assert all(w.week_start.isoweekday() == 1 for w in weeks)  # ISO weeks start on Monday
    assert sum(w.days for w in weeks) == len(daily)
    for w in weeks:
        in_week = [
            daily[w.week_start + timedelta(days=n)] for n in range(7) if w.week_start + timedelta(days=n) in daily
        ]
        last = in_week[-1]
        assert w.days == len(in_week)
        assert w.load == pytest.approx(sum(r.load for r in in_week))
        assert w.sessions == sum(r.sessions for r in in_week)
        assert (w.fitness, w.fatigue, w.form, w.ramp) == (last.fitness, last.fatigue, last.form, last.ramp)
        assert w.fitness_reliable == last.fitness_reliable


@pytest.fixture
def cli_env(migrated: PgDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)


def test_cli_analyze_prints_counts_only(
    cli_env: None,
    app_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    setup(app_sessions)
    monkeypatch.setenv("ANALYTICS_TRIMP_B", "1.92")

    with caplog.at_level(logging.DEBUG):
        result = CliRunner().invoke(app, ["analyze"])

    assert result.exit_code == 0, result.output
    counts = json.loads(result.stdout)["counts"]
    assert counts["daily_load_rows"] == 15
    assert all(isinstance(v, int) for v in counts.values())
    shown = result.output + caplog.text
    for r in rows(app_sessions):
        for value in (r.fitness, r.fatigue, r.form):
            if value:
                assert f"{value:.2f}" not in shown and f"{value:.1f}" not in shown
