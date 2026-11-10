"""HR zone history, the threshold timeline, renormalize and their commands, against a real
Postgres as the app role. Synthetic values only."""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from typer.testing import CliRunner

from garminagent import cli
from garminagent.analytics import analyze
from garminagent.analytics.thresholds import load_rows, set_manual, thresholds_at
from garminagent.cli import app
from garminagent.normalize import HrZonesRecord, parse_activities, parse_daily_summaries
from garminagent.renormalize import renormalize
from garminagent.storage.db import SYNC_LOCK_KEY
from garminagent.storage.models import Activity, AthleteThreshold, AthleteZones, DailySummary
from garminagent.storage.repository import (
    ensure_athlete,
    save_hr_zones,
    save_raw_payload,
    upsert_activities,
    upsert_daily_summaries,
)
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration

D1, D2, D3 = date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def zones(sport: str = "DEFAULT", **values: Any) -> HrZonesRecord:
    base: dict[str, Any] = {
        "training_method": "HR_MAX",
        "zone1_floor": 100,
        "max_hr": 190,
        "resting_hr": 48,
        "lthr": 168,
    }
    return HrZonesRecord(sport=sport, **{**base, **values})


def zone_history(sessions: sessionmaker[Session]) -> list[tuple[str, date, int | None]]:
    with sessions() as session:
        rows = session.execute(
            select(AthleteZones.sport, AthleteZones.valid_from, AthleteZones.lthr).order_by(
                AthleteZones.sport, AthleteZones.valid_from
            )
        ).all()
    return [(r.sport, r.valid_from, r.lthr) for r in rows]


def test_zones_add_a_row_only_when_a_value_changes(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        assert save_hr_zones(session, athlete, [zones(), zones("RUNNING")], None, D1) == 2
        assert save_hr_zones(session, athlete, [zones(), zones("RUNNING")], None, D2) == 0
        assert save_hr_zones(session, athlete, [zones(lthr=170), zones("RUNNING")], None, D3) == 1
        assert save_hr_zones(session, athlete, [zones(lthr=171)], None, D3) == 1  # same day: replaced
    assert zone_history(app_sessions) == [("DEFAULT", D1, 168), ("DEFAULT", D3, 171), ("RUNNING", D1, 168)]


def activity_item(activity_id: int, day: date, sport: str = "running", **fields: Any) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "startTimeGMT": f"{day} 05:00:00",
        "startTimeLocal": f"{day} 07:00:00",
        "activityType": {"typeKey": sport},
        **fields,
    }


def store_activities(sessions: sessionmaker[Session], items: list[dict[str, Any]]) -> int:
    with sessions.begin() as session:
        athlete = ensure_athlete(session)
        upsert_activities(session, athlete, parse_activities(items).records)
    return athlete


def test_analyze_builds_the_timeline_and_keeps_manual_rows(app_sessions: sessionmaker[Session]) -> None:
    athlete = store_activities(
        app_sessions,
        [
            activity_item(1, D1, lactateThresholdBpm=165.0),
            activity_item(2, D2, "gravel_cycling", maxFtp=250.0),
            activity_item(3, D3, "walking"),
        ],
    )
    with app_sessions.begin() as session:
        save_hr_zones(session, athlete, [zones()], None, D2)
        set_manual(session, athlete, "ftp", "ride", D3, 260.0)

    result = analyze(app_sessions, athlete)

    assert result.counts["thresholds_activity"] == 2
    assert result.counts["thresholds_garmin_zones"] == 3
    assert result.counts["thresholds_manual"] == 1
    assert result.counts["sport_other"] == 1
    assert result.counts["unmapped_activities"] == 1
    assert result.unmapped_keys == ["walking"]
    with app_sessions() as session:
        on_d1 = thresholds_at(session, athlete, D1, "run")
        on_d2 = thresholds_at(session, athlete, D2, "run")
        ride = thresholds_at(session, athlete, D3, "ride")
    assert on_d1["lthr"] is not None and (on_d1["lthr"].value, on_d1["lthr"].source) == (165.0, "activity")
    assert on_d2["lthr"] is not None and (on_d2["lthr"].value, on_d2["lthr"].source) == (168.0, "garmin_zones")
    assert ride["ftp"] is not None and (ride["ftp"].value, ride["ftp"].source) == (260.0, "manual")
    assert on_d1["ftp"] is None

    # Inputs change: derived rows follow, touching only what differs; manual rows stay.
    store_activities(app_sessions, [activity_item(1, D1, lactateThresholdBpm=166.0)])
    again = analyze(app_sessions, athlete)
    assert (again.counts["thresholds_inserted"], again.counts["thresholds_updated"]) == (0, 1)
    with app_sessions() as session:
        manual = load_rows(session, athlete, ("manual",))
    assert [(r.value_type, r.sport, r.valid_from, r.value) for r in manual] == [("ftp", "ride", D3, 260.0)]

    with app_sessions.begin() as session:
        session.execute(update(Activity).values(lactate_threshold_hr=None))
    assert analyze(app_sessions, athlete).counts["thresholds_deleted"] == 1


def test_renormalize_uses_the_newest_payload_and_fills_new_columns(app_sessions: sessionmaker[Session]) -> None:
    old = [activity_item(1, D1), activity_item(2, D2)]
    new = [activity_item(1, D1, lactateThresholdBpm=165.0, maxFtp=250.0), activity_item(3, D3)]
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        save_raw_payload(session, athlete, "activities", "page-a", 200, old, T0)
        newest = save_raw_payload(session, athlete, "activities", "page-b", 200, new, T0 + timedelta(days=1))
        save_raw_payload(session, athlete, "daily_summaries", "range", 200, {"data": []}, T0)
        # As stored before M6a: the rows exist, without the new columns.
        upsert_activities(session, athlete, parse_activities([activity_item(1, D1)]).records)

    counts = renormalize(app_sessions, athlete, "activities")

    assert counts == {"payloads": 2, "records": 3, "superseded": 1, "skipped": 0}
    with app_sessions() as session:
        first = session.get_one(Activity, (athlete, 1))
        assert (first.lactate_threshold_hr, first.ftp_w, first.raw_payload_id) == (165.0, 250.0, newest)
        assert session.scalar(select(func.count()).select_from(Activity)) == 3


def test_renormalize_applies_range_checks_to_old_rows(app_sessions: sessionmaker[Session]) -> None:
    payload = {"data": [{"calendarDate": "2026-03-01", "heartRate": {"restingValue": -1}}]}
    with app_sessions.begin() as session:
        athlete = ensure_athlete(session)
        save_raw_payload(session, athlete, "daily_summaries", "2026-03-01..2026-03-01", 200, payload, T0)
        # A row written before ADR 0015's range checks, holding the "no data" marker.
        (record,) = parse_daily_summaries({"data": [{"calendarDate": "2026-03-01"}]}).records
        upsert_daily_summaries(session, athlete, [record])
        session.execute(update(DailySummary).values(resting_hr=-1))

    renormalize(app_sessions, athlete, "daily_summaries")

    with app_sessions() as session:
        assert session.get_one(DailySummary, (athlete, D1)).resting_hr is None


@pytest.fixture
def cli_env(migrated: PgDatabase, app_sessions: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", migrated.app_url)


def invoke(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def test_cli_thresholds_set_list_and_unset(cli_env: None, app_sessions: sessionmaker[Session]) -> None:
    result = invoke("thresholds", "set", "lthr", "168", "--sport", "run", "--from", "2026-03-01")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"value_type": "lthr", "sport": "run", "valid_from": "2026-03-01"}

    listed = invoke("thresholds", "list", "--at", "2026-03-05", "--sport", "run")
    assert listed.exit_code == 0, listed.output
    out = json.loads(listed.stdout)
    assert out["timeline"] == [
        {"value_type": "lthr", "sport": "run", "source": "manual", "valid_from": "2026-03-01", "value": 168.0}
    ]
    assert out["at"]["values"]["lthr"] == {
        "value": 168.0,
        "source": "manual",
        "sport": "run",
        "valid_from": "2026-03-01",
        "backfilled": False,
    }
    before = json.loads(invoke("thresholds", "list", "--at", "2026-02-01", "--sport", "run").stdout)
    assert before["at"]["values"]["lthr"]["backfilled"] is True  # the owner's backward fill
    assert out["at"]["values"]["ftp"] is None

    assert invoke("thresholds", "unset", "lthr", "--sport", "run", "--from", "2026-03-01").exit_code == 0
    assert invoke("thresholds", "unset", "lthr", "--sport", "run", "--from", "2026-03-01").exit_code == 1
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(AthleteThreshold)) == 0


@pytest.mark.parametrize(
    ("kind", "value"),
    [("lthr", "45"), ("max_hr", "260"), ("ftp", "-1"), ("threshold_speed", "36"), ("resting_hr", "nan")],
)
def test_cli_refuses_out_of_range_thresholds(
    kind: str, value: str, cli_env: None, app_sessions: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    result = invoke("thresholds", "set", kind, "--", value)

    assert result.exit_code == 2
    with app_sessions() as session:
        assert session.scalar(select(func.count()).select_from(AthleteThreshold)) == 0
    assert value not in " ".join(r.getMessage() for r in caplog.records)


def test_cli_rejects_unknown_codes(cli_env: None) -> None:
    assert invoke("thresholds", "set", "vo2max", "50").exit_code == 2
    assert invoke("thresholds", "set", "lthr", "168", "--sport", "swim").exit_code == 2
    assert invoke("renormalize", "steps").exit_code == 2


def test_cli_analyze_and_renormalize_print_counts_only(cli_env: None, app_sessions: sessionmaker[Session]) -> None:
    store_activities(app_sessions, [activity_item(1, D1, "yoga", lactateThresholdBpm=165.0)])

    analyzed = invoke("analyze")
    assert analyzed.exit_code == 0, analyzed.output
    out = json.loads(analyzed.stdout)
    assert out["counts"]["activities"] == 1
    assert out["counts"]["thresholds_activity"] == 1  # LTHR counts for its own group, here `other`
    assert out["unmapped_sport_keys"] == ["yoga"]
    assert "165" not in analyzed.output

    renormalized = invoke("renormalize", "activities")
    assert renormalized.exit_code == 0, renormalized.output
    assert json.loads(renormalized.stdout) == {"payloads": 0, "records": 0, "superseded": 0, "skipped": 0}


def test_cli_analyze_waits_for_a_running_sync(cli_env: None, owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        conn.exec_driver_sql(f"SELECT pg_advisory_lock({SYNC_LOCK_KEY})")
        try:
            result = invoke("analyze")
            renormalized = invoke("renormalize", "activities")
        finally:
            conn.exec_driver_sql(f"SELECT pg_advisory_unlock({SYNC_LOCK_KEY})")
    assert (result.exit_code, renormalized.exit_code) == (0, 0)
    assert json.loads(result.stdout) == {"stopped": "already_running"}


def test_cli_unexpected_error_exits_1_with_the_class_name_only(cli_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object) -> None:
        raise ValueError("synthetic failure 4d2b")

    monkeypatch.setattr(cli, "run_renormalize", broken)

    result = invoke("renormalize", "activities")

    assert result.exit_code == 1
    assert "renormalize failed: unexpected error ValueError" in result.stderr  # the CLI logs to stderr
    assert "4d2b" not in result.output + result.stderr  # no message, no traceback
