"""`activity_metrics` through `analyze`, against a real Postgres as the app role: what is stored,
when it is recomputed, the per-run cap, and that only counts leave the database. Synthetic
values only."""

import json
import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent.analytics import AnalyticsOptions, analyze
from garminagent.analytics.activity_metrics import ActivityInput, Resolution, fingerprint
from garminagent.analytics.metrics import ALGO_VERSION
from garminagent.analytics.thresholds import Resolved, set_manual
from garminagent.analytics.zones import ZoneFloors
from garminagent.cli import app
from garminagent.fit import ParsedFit, Sample
from garminagent.normalize import DailySummaryRecord, parse_activities
from garminagent.storage.models import Activity, ActivityFile, ActivityMetrics
from garminagent.storage.repository import (
    ensure_athlete,
    replace_activity_samples,
    save_hr_zones,
    upsert_activities,
    upsert_daily_summaries,
)
from tests.storage.conftest import PgDatabase
from tests.storage.test_thresholds import activity_item, zones

pytestmark = pytest.mark.integration

D0 = date(2026, 3, 1)
B = 1.92
ON = AnalyticsOptions(trimp_b=B)
# Zones: max 190, LTHR 162; resting 50 from the daily summaries. HR 162 for an hour = 100.
ZONES = zones(max_hr=190, lthr=162, resting_hr=None, zone1_floor=100, zone2_floor=120, zone3_floor=140,
              zone4_floor=160, zone5_floor=180)  # fmt: skip


def add_activity(
    sessions: sessionmaker[Session],
    activity_id: int,
    day: date,
    sport: str = "running",
    *,
    seconds: int = 3600,
    hr: int | None = 162,
    power: int | None = None,
    parser_version: int = 1,
) -> int:
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
        samples = [Sample(t, heart_rate=hr, power_w=power) for t in range(seconds)]
        replace_activity_samples(
            session, athlete, activity_id, ParsedFit(datetime(2026, 3, 1, tzinfo=UTC), samples, [])
        )
    return athlete


def add_resting_days(sessions: sessionmaker[Session], athlete: int, first: date, days: int, value: int = 50) -> None:
    records = [DailySummaryRecord(calendar_date=first + timedelta(days=n), resting_hr=value) for n in range(days)]
    with sessions.begin() as session:
        upsert_daily_summaries(session, athlete, records)


def stored(sessions: sessionmaker[Session], activity_id: int) -> ActivityMetrics:
    with sessions() as session:
        row = session.scalar(select(ActivityMetrics).where(ActivityMetrics.activity_id == activity_id))
        assert row is not None
        session.expunge(row)
        return row


def setup_run(sessions: sessionmaker[Session]) -> int:
    """A run on D0 + 7 at LTHR for an hour, zones seen on D0 + 10 (so filled back), resting HR
    from the week before."""
    athlete = add_activity(sessions, 1, D0 + timedelta(days=7))
    add_resting_days(sessions, athlete, D0, 7)
    with sessions.begin() as session:
        save_hr_zones(session, athlete, [ZONES], None, D0 + timedelta(days=10))
    return athlete


def test_analyze_stores_metrics_and_the_thresholds_used(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup_run(app_sessions)

    result = analyze(app_sessions, athlete, ON)

    assert result.counts["metrics_computed"] == 1
    assert result.counts["metrics_backfilled"] == 1
    assert result.counts["load_hr"] == 1
    assert "errors" not in result.counts
    row = stored(app_sessions, 1)
    assert (row.activity_date, row.sport_group, row.algo_version) == (D0 + timedelta(days=7), "run", ALGO_VERSION)
    assert row.moving_s == 3600
    assert (row.load_method, row.load, row.hr_load) == ("hr", pytest.approx(100.0), pytest.approx(100.0))
    assert (row.power_load, row.weighted_power_w) == (None, None)
    assert (row.hr_zone_4_s, row.three_zone_high_s, row.hr_zone_0_s) == (3600, 3600, 0)
    assert (row.lthr_bpm, row.max_hr_bpm, row.resting_hr_bpm) == (162.0, 190.0, 50.0)
    assert (row.resting_hr_source, row.hr_zones_sport, row.hr_zones_valid_from) == (
        "daily_summary",
        "DEFAULT",
        D0 + timedelta(days=10),
    )
    assert row.thresholds_backfilled  # zones and LTHR/max HR come from a later fetch
    assert row.ftp_w is None
    assert len(row.input_fingerprint) == 64


def test_recompute_only_when_inputs_change_not_on_refetch(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup_run(app_sessions)
    analyze(app_sessions, athlete, ON)
    first = stored(app_sessions, 1)

    # A refetch rewrites the activity and bumps its updated_at; the metrics stay as they are.
    with app_sessions.begin() as session:
        upsert_activities(session, athlete, parse_activities([activity_item(1, D0 + timedelta(days=7))]).records)
    again = analyze(app_sessions, athlete, ON)
    assert (again.counts["metrics_computed"], again.counts["metrics_current"]) == (0, 1)
    assert stored(app_sessions, 1).updated_at == first.updated_at

    # A threshold change on or before the day recomputes.
    with app_sessions.begin() as session:
        set_manual(session, athlete, "lthr", "run", D0, 170.0)
    changed = analyze(app_sessions, athlete, ON)
    assert changed.counts["metrics_computed"] == 1
    assert stored(app_sessions, 1).lthr_bpm == 170.0

    # So does a newer parser version (samples were replaced) and the coefficient.
    with app_sessions.begin() as session:
        session.execute(update(ActivityFile).values(parser_version=2))
    assert analyze(app_sessions, athlete, ON).counts["metrics_computed"] == 1
    assert analyze(app_sessions, athlete, AnalyticsOptions(trimp_b=1.67)).counts["metrics_computed"] == 1


def test_without_the_coefficient_hr_load_waits_and_is_counted(
    app_sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    athlete = setup_run(app_sessions)

    with caplog.at_level(logging.WARNING):
        result = analyze(app_sessions, athlete, AnalyticsOptions(trimp_b=None))

    assert result.counts["hr_load_not_configured"] == 1
    assert result.counts["load_none"] == 1
    assert "ANALYTICS_TRIMP_B is not set" in caplog.text
    assert (stored(app_sessions, 1).load_method, stored(app_sessions, 1).hr_load) == ("none", None)
    # Setting it changes every fingerprint: the row is recomputed with HR load.
    assert analyze(app_sessions, athlete, ON).counts["load_hr"] == 1


def test_the_per_run_cap_spreads_the_catch_up_newest_first(app_sessions: sessionmaker[Session]) -> None:
    athlete = 0
    for n in range(3):
        athlete = add_activity(app_sessions, n + 1, D0 + timedelta(days=n), seconds=60)

    first = analyze(app_sessions, athlete, AnalyticsOptions(trimp_b=B, max_activities=2))
    assert (first.counts["metrics_computed"], first.counts["metrics_pending"]) == (2, 1)
    with app_sessions() as session:
        done = set(session.scalars(select(ActivityMetrics.activity_id)))
    assert done == {2, 3}  # the newest two

    second = analyze(app_sessions, athlete, AnalyticsOptions(trimp_b=B, max_activities=2))
    assert (second.counts["metrics_computed"], second.counts["metrics_pending"]) == (1, 0)


def test_activity_without_samples_gets_a_row_without_load(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        upsert_activities(session, athlete, parse_activities([activity_item(9, D0, "walking")]).records)

    result = analyze(app_sessions, athlete, ON)

    assert result.counts["metrics_no_samples"] == 1
    row = stored(app_sessions, 9)
    assert (row.moving_s, row.load, row.load_method, row.sport_group) == (None, None, "none", "other")


def test_ride_with_power_and_a_manual_ftp_uses_power_load(app_sessions: sessionmaker[Session]) -> None:
    athlete = add_activity(app_sessions, 5, D0, "gravel_cycling", hr=140, power=200, seconds=1800)
    with app_sessions.begin() as session:
        set_manual(session, athlete, "ftp", "ride", D0, 250.0)

    analyze(app_sessions, athlete, ON)

    row = stored(app_sessions, 5)
    # 30 min at 200 W, FTP 250: I = 0.8, load 32.0 (as in tests/test_metrics.py)
    assert (row.load_method, row.weighted_power_w, row.intensity) == ("power", 200.0, pytest.approx(0.8))
    assert row.power_load == pytest.approx(32.0)
    assert row.power_level_3_s == 1800  # 80 % of FTP
    assert row.hr_load is None  # no max HR or LTHR for this athlete


def test_deleting_an_activity_deletes_its_metrics(app_sessions: sessionmaker[Session]) -> None:
    athlete = setup_run(app_sessions)
    analyze(app_sessions, athlete, ON)
    with app_sessions.begin() as session:
        session.execute(delete(Activity))  # the FK cascades; deletions at Garmin aren't reconciled
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(ActivityMetrics)) == 0


def test_fingerprint_covers_every_input() -> None:
    activity = ActivityInput(1, D0, "run", (("ab" * 32, "parsed", 1),))
    lthr = Resolved(162.0, "garmin_zones", "all", D0)
    resolution = Resolution({"ftp": None, "lthr": lthr, "max_hr": None, "resting_hr": None}, None)
    base = fingerprint(activity, resolution, B)
    assert fingerprint(activity, resolution, B) == base

    def with_lthr(r: Resolved) -> Resolution:
        return Resolution({**resolution.thresholds, "lthr": r}, None)

    zones_row = (100, 120, 140, 160, 180)
    variants = [
        fingerprint(activity, resolution, 1.67),
        fingerprint(activity, resolution, None),
        fingerprint(ActivityInput(1, D0 + timedelta(days=1), "run", activity.fit_files), resolution, B),
        fingerprint(ActivityInput(1, D0, "ski", activity.fit_files), resolution, B),
        fingerprint(ActivityInput(1, D0, "run", (("ab" * 32, "parsed", 2),)), resolution, B),
        fingerprint(ActivityInput(1, D0, "run", ()), resolution, B),
        fingerprint(activity, with_lthr(Resolved(163.0, "garmin_zones", "all", D0)), B),
        fingerprint(activity, with_lthr(Resolved(162.0, "garmin_zones", "all", D0, backfilled=True)), B),
        fingerprint(activity, Resolution(resolution.thresholds, ZoneFloors("DEFAULT", D0, zones_row)), B),
    ]
    assert base not in variants and len(set(variants)) == len(variants)


# --- only counts leave the database ---


@pytest.fixture
def cli_env(migrated: PgDatabase, app_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)


def test_cli_analyze_full_prints_counts_only_and_never_the_coefficient(
    cli_env: None,
    app_sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Distinctive synthetic values, so any leak of a metric or threshold is recognisable.
    athlete = add_activity(app_sessions, 7, D0 + timedelta(days=7), hr=163, seconds=3721)
    add_resting_days(app_sessions, athlete, D0, 7, value=47)
    with app_sessions.begin() as session:
        save_hr_zones(session, athlete, [ZONES], None, D0)
    monkeypatch.setenv("ANALYTICS_TRIMP_B", "1.67")
    monkeypatch.setenv("ANALYTICS_MAX_ACTIVITIES", "1")
    add_activity(app_sessions, 8, D0 + timedelta(days=8), seconds=60)

    with caplog.at_level(logging.DEBUG):
        capped = CliRunner().invoke(app, ["analyze"])
        full = CliRunner().invoke(app, ["analyze", "--full"])

    assert capped.exit_code == 0, capped.output
    assert json.loads(capped.stdout)["counts"]["metrics_pending"] == 1
    assert full.exit_code == 0, full.output
    out = json.loads(full.stdout)
    assert (out["counts"]["metrics_computed"], out["counts"]["metrics_pending"]) == (1, 0)
    assert all(isinstance(v, int) for v in out["counts"].values())
    row = stored(app_sessions, 7)
    shown = full.output + capped.output + caplog.text
    for value in (row.load, row.hr_load, row.resting_hr_bpm, row.max_hr_bpm, row.moving_s):
        assert value is not None
        assert f"{value}" not in shown and f"{value:.1f}" not in shown
    assert "1.67" not in shown
    config = CliRunner().invoke(app, ["config"])
    assert '"analytics_trimp_b": "set"' in config.output and "1.67" not in config.output
