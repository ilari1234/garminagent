"""`activity_best_effort` and its views through `analyze`, against a real Postgres as the app
role: what is stored, when it is recomputed, the per-run cap, the curve and PB views, and that
only counts leave the database. Synthetic values only."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.analytics import AnalyticsOptions, analyze
from garminagent.cli import app
from garminagent.fit import ParsedFit, Sample
from garminagent.normalize import parse_activities
from garminagent.storage.models import Activity, ActivityBestEffort, ActivityFile
from garminagent.storage.repository import ensure_athlete, replace_activity_samples, upsert_activities
from tests.storage.conftest import PgDatabase
from tests.storage.test_thresholds import activity_item

pytestmark = pytest.mark.integration

# Relative to today, far from midnight edges, so the curve's periods are stable in any zone.
TODAY = date.today()
FULL = AnalyticsOptions(max_activities=None)


def add_activity(
    sessions: sessionmaker[Session],
    activity_id: int,
    day: date,
    sport: str = "running",
    *,
    seconds: int = 600,
    speed: float | None = 4.0,
    power: int | None = None,
    parser_version: int = 1,
) -> int:
    """An activity at a constant speed (distance from 0) and power, sampled every second."""
    with sessions.begin() as session:
        athlete = ensure_athlete(session)
        upsert_activities(session, athlete, parse_activities([activity_item(activity_id, day, sport)]).records)
        session.add(
            ActivityFile(
                athlete_id=athlete,
                activity_id=activity_id,
                kind="fit",
                blob_key=f"{athlete}/activities/{activity_id}/fit-{activity_id:064x}.fit",
                sha256=f"{activity_id:064x}",
                size_bytes=1,
                fetched_at=datetime(2026, 3, 1, tzinfo=UTC),
                parse_status="parsed",
                parser_version=parser_version,
            )
        )
        samples = [Sample(t, power_w=power, distance_m=None if speed is None else speed * t) for t in range(seconds)]
        replace_activity_samples(
            session, athlete, activity_id, ParsedFit(datetime(2026, 3, 1, tzinfo=UTC), samples, [])
        )
    return athlete


def efforts(sessions: sessionmaker[Session], activity_id: int) -> dict[tuple[str, int], float | None]:
    with sessions() as session:
        rows = session.scalars(select(ActivityBestEffort).where(ActivityBestEffort.activity_id == activity_id))
        return {(r.kind, r.span): r.seconds if r.kind == "distance" else r.power_w for r in rows}


def test_analyze_stores_a_row_for_every_distance_and_window(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=3), seconds=1300)  # 5196 m at 4 m/s
    add_activity(app_sessions, 2, TODAY - timedelta(days=3), "cycling", seconds=900, speed=10.0, power=200)
    add_activity(app_sessions, 3, TODAY - timedelta(days=3), "strength_training", speed=None)

    result = analyze(app_sessions, athlete, FULL)

    assert result.counts["best_effort_computed"] == 3
    assert result.counts["best_effort_with_distance"] == 1  # the ride's 9 km reaches no ride distance
    assert result.counts["best_effort_with_power"] == 1
    assert (result.counts["best_effort_spikes"], result.counts["best_effort_counter_drops"]) == (0, 0)
    run = efforts(app_sessions, 1)
    assert len(run) == 7 + 12
    assert run[("distance", 400)] == pytest.approx(100.0)
    assert run[("distance", 5000)] == pytest.approx(1250.0)
    assert run[("distance", 10000)] is None
    ride = efforts(app_sessions, 2)
    assert len(ride) == 4 + 12
    assert ride[("power", 600)] == 200.0
    assert ride[("power", 1200)] is None
    assert len(efforts(app_sessions, 3)) == 12


def test_unchanged_inputs_are_not_recomputed_and_a_new_parser_is(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=3))
    analyze(app_sessions, athlete, FULL)
    with app_sessions() as session:
        before = session.scalar(select(ActivityBestEffort.updated_at).limit(1))

    again = analyze(app_sessions, athlete, FULL)

    assert (again.counts["best_effort_computed"], again.counts["best_effort_current"]) == (0, 1)
    with app_sessions() as session:
        assert session.scalar(select(ActivityBestEffort.updated_at).limit(1)) == before
    with app_sessions.begin() as session:
        session.execute(update(ActivityFile).values(parser_version=2))
    assert analyze(app_sessions, athlete, FULL).counts["best_effort_computed"] == 1


def test_a_new_sport_replaces_the_activitys_rows(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=3))
    analyze(app_sessions, athlete, FULL)
    with app_sessions.begin() as session:
        session.execute(update(Activity).values(sport="cycling"))

    analyze(app_sessions, athlete, FULL)

    rows = efforts(app_sessions, 1)
    assert sorted(span for kind, span in rows if kind == "distance") == [10000, 20000, 40000, 100000]
    with app_sessions() as session:
        assert set(session.scalars(select(ActivityBestEffort.sport_group))) == {"ride"}


def test_the_cap_leaves_the_rest_pending_newest_first(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=5))
    add_activity(app_sessions, 2, TODAY - timedelta(days=4))
    add_activity(app_sessions, 3, TODAY - timedelta(days=3))

    capped = analyze(app_sessions, athlete, AnalyticsOptions(max_activities=2))

    assert (capped.counts["best_effort_computed"], capped.counts["best_effort_pending"]) == (2, 1)
    assert efforts(app_sessions, 1) == {}
    assert efforts(app_sessions, 3)
    assert analyze(app_sessions, athlete, FULL).counts["best_effort_computed"] == 1


def test_deleting_an_activity_removes_its_efforts(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=3))
    analyze(app_sessions, athlete, FULL)
    with app_sessions.begin() as session:
        session.execute(text("DELETE FROM activity"))
        assert session.execute(text("SELECT count(*) FROM activity_best_effort")).scalar_one() == 0


def curve(sessions: sessionmaker[Session], kind: str, span: int) -> dict[int | None, tuple[float, int]]:
    with sessions() as session:
        return {
            r.period_days: (r.seconds if kind == "distance" else r.power_w, r.activity_id)
            for r in session.execute(
                text(
                    "SELECT period_days, seconds, power_w, activity_id FROM best_effort_curve"
                    " WHERE sport_group = 'run' AND kind = :k AND span = :s"
                ),
                {"k": kind, "s": span},
            )
        }


def test_the_curve_takes_the_best_of_each_period(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=500), speed=5.0, power=300)  # fastest, oldest
    add_activity(app_sessions, 2, TODAY - timedelta(days=200), speed=4.5, power=280)
    add_activity(app_sessions, 3, TODAY - timedelta(days=60), speed=4.0, power=260)
    add_activity(app_sessions, 4, TODAY - timedelta(days=10), speed=3.0, power=240)
    add_activity(app_sessions, 5, TODAY - timedelta(days=5), speed=None, power=None)  # nothing to offer
    analyze(app_sessions, athlete, FULL)

    assert curve(app_sessions, "distance", 400) == {
        None: (pytest.approx(80.0), 1),
        365: (pytest.approx(400 / 4.5), 2),
        90: (pytest.approx(100.0), 3),
        42: (pytest.approx(400 / 3), 4),
    }
    assert {p: a for p, (_, a) in curve(app_sessions, "power", 60).items()} == {None: 1, 365: 2, 90: 3, 42: 4}
    assert curve(app_sessions, "distance", 10000) == {}


def test_a_tie_goes_to_whoever_set_it_first(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 2, TODAY - timedelta(days=20))
    add_activity(app_sessions, 1, TODAY - timedelta(days=10))
    analyze(app_sessions, athlete, FULL)

    assert curve(app_sessions, "distance", 400)[None][1] == 2


def test_the_pb_view_shows_the_best_and_the_one_before(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=300), speed=4.0)
    add_activity(app_sessions, 2, TODAY - timedelta(days=200), speed=3.5)
    add_activity(app_sessions, 3, TODAY - timedelta(days=100), speed=5.0)
    add_activity(app_sessions, 4, TODAY - timedelta(days=50), speed=4.5)
    analyze(app_sessions, athlete, FULL)

    with app_sessions() as session:
        pb = session.execute(
            text("SELECT * FROM best_effort_pb WHERE sport_group = 'run' AND kind = 'distance' AND span = 400")
        ).one()
        first = session.execute(
            text("SELECT * FROM best_effort_pb WHERE kind = 'power' AND span = 5")  # no power anywhere
        ).all()

    assert (pb.activity_id, pb.seconds) == (3, pytest.approx(80.0))
    assert (pb.previous_activity_id, pb.previous_seconds) == (1, pytest.approx(100.0))
    assert pb.previous_activity_date == TODAY - timedelta(days=300)
    assert first == []


def test_a_first_effort_has_no_previous_best(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=30))
    analyze(app_sessions, athlete, FULL)

    with app_sessions() as session:
        pb = session.execute(text("SELECT * FROM best_effort_pb WHERE kind = 'distance' AND span = 400")).one()

    assert (pb.activity_id, pb.previous_activity_id, pb.previous_seconds) == (1, None, None)


@pytest.fixture
def cli_env(migrated: PgDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)


def test_cli_analyze_prints_counts_only(
    cli_env: None, app_sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    add_activity(app_sessions, 1, TODAY - timedelta(days=3), speed=3.7, power=217)

    with caplog.at_level(logging.DEBUG):
        result = CliRunner().invoke(app, ["analyze", "--full"])

    assert result.exit_code == 0, result.output
    counts = json.loads(result.stdout)["counts"]
    assert counts["best_effort_computed"] == 1
    assert all(isinstance(v, int) for v in counts.values())
    shown = result.output + caplog.text
    for value in efforts(app_sessions, 1).values():
        if value is not None:
            assert f"{value:.1f}" not in shown
    assert "217" not in shown


def db_today(sessions: sessionmaker[Session]) -> date:
    with sessions() as session:
        return session.execute(text("SELECT CURRENT_DATE")).scalar_one()


def test_a_period_covers_today_and_the_days_before_it(app_sessions: sessionmaker[Session]) -> None:
    today = db_today(app_sessions)  # the view's own "today", so the boundary is exact
    athlete = add_activity(app_sessions, 1, today - timedelta(days=42), speed=5.0)
    add_activity(app_sessions, 2, today - timedelta(days=41), speed=4.0)
    add_activity(app_sessions, 3, today - timedelta(days=90), speed=6.0)
    analyze(app_sessions, athlete, FULL)

    best = {p: a for p, (_, a) in curve(app_sessions, "distance", 400).items()}

    assert best == {None: 3, 365: 3, 90: 1, 42: 2}


def test_a_same_day_tie_goes_to_the_lower_activity_id(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 5, TODAY - timedelta(days=10))
    add_activity(app_sessions, 3, TODAY - timedelta(days=10))
    analyze(app_sessions, athlete, FULL)

    assert curve(app_sessions, "distance", 400)[None][1] == 3


def test_another_effort_on_the_pbs_day_is_not_the_previous_best(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 1, TODAY - timedelta(days=100), speed=4.0)
    add_activity(app_sessions, 2, TODAY - timedelta(days=50), speed=5.0)
    add_activity(app_sessions, 3, TODAY - timedelta(days=50), speed=4.5)
    analyze(app_sessions, athlete, FULL)

    with app_sessions() as session:
        pb = session.execute(text("SELECT * FROM best_effort_pb WHERE kind = 'distance' AND span = 400")).one()

    assert (pb.activity_id, pb.previous_activity_id) == (2, 1)
