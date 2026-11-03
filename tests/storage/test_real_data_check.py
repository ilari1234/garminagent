import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from garminagent.fit import Lap, ParsedFit, Sample
from garminagent.normalize import parse_activities, parse_weight
from garminagent.storage.repository import (
    SyncRange,
    ensure_athlete,
    record_sync_run,
    replace_activity_samples,
    save_raw_payload,
    set_cursor,
    upsert_activities,
    upsert_weight,
)
from tests.garmin.fakes import load_fixture

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "real_data_check.py"
TODAY = date(2026, 10, 10)
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
# Distinctive synthetic values, so the test can prove none of them reaches the report.
WEIGHT_GRAMS = 71234.0
HEART_RATE = 147


class _Check(Protocol):
    def collect(self, session: Session, today: date | None = None) -> dict[str, Any]: ...


def _load_script() -> _Check:
    spec = importlib.util.spec_from_file_location("real_data_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(_Check, module)


def _weight_payload() -> dict[str, Any]:
    day = {"summaryDate": "2026-10-09", "numOfWeightEntries": 1, "latestWeight": {"weight": WEIGHT_GRAMS, "bmi": 22.1}}
    return {"dailyWeightSummaries": [day, {**day, "summaryDate": "2026-10-10"}]}


def _sleep_payload() -> list[dict[str, Any]]:
    night = {
        "calendarDate": "2026-10-09",
        "sleepTimeSeconds": 25210,
        "avgHeartRate": None,
        "sleepScores": {"overall": {"value": 81}},
    }
    return [night, {**night, "calendarDate": "2026-10-10", "sleepScores": None}]


def _seed(sessions: sessionmaker[Session]) -> None:
    activities = parse_activities(load_fixture("activities_search.json")).records
    weight = _weight_payload()
    with sessions.begin() as session:
        athlete = ensure_athlete(session)
        upsert_activities(session, athlete, activities)
        raw_id = save_raw_payload(session, athlete, "weight", "2026-10-09..2026-10-10", 200, weight, T0)
        upsert_weight(session, athlete, parse_weight(weight).records, raw_id)
        set_cursor(session, athlete, "weight", SyncRange(date(2026, 10, 9), TODAY))
        save_raw_payload(session, athlete, "sleep", "2026-10-09..2026-10-10", 200, _sleep_payload(), T0)
        # Every 2 s for a minute, then one sample after a pause of 342 s.
        samples = [Sample(elapsed_s=s, heart_rate=HEART_RATE, power_w=201) for s in (*range(0, 60, 2), 400)]
        parsed = ParsedFit(start_time=T0, samples=samples, laps=[Lap(lap_index=0, elapsed_s=60.0)])
        replace_activity_samples(session, athlete, activities[0].activity_id, parsed)
        record_sync_run(
            session,
            athlete,
            started_at=T0 - timedelta(minutes=5),
            finished_at=T0,
            stopped="budget",
            requests=12,
            counts={"weight": {"records": 2}},
        )


def test_report_counts_structure_and_weight_unit(app_sessions: sessionmaker[Session]) -> None:
    _seed(app_sessions)
    with app_sessions() as session:
        report = _load_script().collect(session, TODAY)

    assert report["sync"]["runs_by_stop_reason"] == {"budget": 1}
    assert report["sync"]["datasets"]["weight"] == {"days_synced": 2, "through_last_2_days": True}
    assert report["files"]["activities"] == 5
    assert report["files"]["activity_file_status"] == {"untried": 5}
    assert report["files"]["activities_with_samples"] == 1
    assert report["files"]["activities_with_laps"] == 1
    assert report["wellness"]["weight_daily"] == {
        "rows": 2,
        "days_synced": 2,
        "rows_with": {"weight_kg": 2, "body_fat_pct": 0},
    }
    weight = report["weight_units"]
    assert weight["unit"] == "grams"
    assert weight["latest_weight"] == {"grams_branch_plausible": 2}
    assert weight["weight_keys"] == ["bmi", "weight"]
    samples = report["samples"]
    assert samples["rows"] == 31
    assert samples["rows_with"]["heart_rate"] == 31
    assert samples["rows_with"]["cadence"] == 0
    assert samples["bytes"]["activity_sample"] > 0


def test_report_follow_ups_sleep_keys_gaps_and_sports(app_sessions: sessionmaker[Session]) -> None:
    _seed(app_sessions)
    with app_sessions() as session:
        report = _load_script().collect(session, TODAY)

    assert report["sleep_keys"] == {
        "nights": 2,
        "key_paths_total": 5,
        "non_null_by_key": {
            "avgHeartRate": 0,
            "calendarDate": 2,
            "sleepScores": 1,
            "sleepScores.overall": 1,
            "sleepTimeSeconds": 2,
        },
    }
    gaps = report["sample_gaps_s"]
    assert gaps["2"] == {"count": 29, "seconds": 58}
    assert gaps["over_120"] == {"count": 1, "seconds": 342}
    assert gaps["4-5"] == {"count": 0, "seconds": 0}
    assert report["by_sport"] == {
        "running": {"activities": 3, "with_laps": 1, "with_hr": 1, "with_power": 1},
        "gravel_cycling": {"activities": 1, "with_laps": 0, "with_hr": 0, "with_power": 0},
        "strength_training": {"activities": 1, "with_laps": 0, "with_hr": 0, "with_power": 0},
    }


def test_report_holds_no_values_dates_or_ids(app_sessions: sessionmaker[Session]) -> None:
    _seed(app_sessions)
    with app_sessions() as session:
        output = json.dumps(_load_script().collect(session, TODAY))

    activity_ids = [str(a.activity_id) for a in parse_activities(load_fixture("activities_search.json")).records]
    for forbidden in (str(int(WEIGHT_GRAMS)), "71.234", "2026-10", "25210", *activity_ids):
        assert forbidden not in output
    assert f": {HEART_RATE}" not in output


def test_report_on_an_empty_database(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions() as session:
        report = _load_script().collect(session, TODAY)
    assert report["sync"]["runs_total"] == 0
    assert report["weight_units"]["unit"] == "no data"
    assert report["samples"]["rows"] == 0
    assert report["sleep_keys"]["nights"] == 0
    assert report["sample_gaps_s"]["1"] == {"count": 0, "seconds": 0}
    assert report["by_sport"] == {}
