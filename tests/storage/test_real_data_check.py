import importlib.util
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from sqlalchemy.orm import Session, sessionmaker

from garminagent.analytics.thresholds import rebuild_derived, set_manual
from garminagent.fit import Lap, ParsedFit, Sample
from garminagent.normalize import parse_activities, parse_hr_zones, parse_weight
from garminagent.storage.repository import (
    SyncRange,
    ensure_athlete,
    record_sync_run,
    replace_activity_samples,
    save_hr_zones,
    save_raw_payload,
    set_cursor,
    upsert_activities,
    upsert_weight,
)
from garminagent.sync import ACTIVITIES, HR_ZONES, HR_ZONES_REQUEST_KEY
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


# --- M6a threshold coverage (ADR 0013, real-data findings) ---

# Distinctive synthetic values; none may reach the report.
LTHR_RUN, LTHR_RUN_OLD, LTHR_RIDE = 163.0, 151.0, 158.0
SPEED_CM_PER_MS, SPEED_M_PER_S = 0.3417, 3.417
FTP, MANUAL_FTP, TSS = 251.0, 243.0, 77.7
IF_FRACTION, IF_PERCENT = 0.83, 83.0
THRESHOLD_IDS = (9100000001, 9100000002, 9100000003, 9100000004)


def _activity(activity_id: int, type_key: str, day: str, **fields: float) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "startTimeGMT": f"{day} 07:00:00",
        "activityType": {"typeKey": type_key},
        "duration": 3600.0,
        **fields,
    }


def _zones_payload() -> list[dict[str, Any]]:
    floors = {"zone1Floor": 101, "zone2Floor": 121, "zone3Floor": 141, "zone4Floor": 156, "zone5Floor": 171}
    return [
        {
            "sport": "DEFAULT",
            "trainingMethod": "HR_MAX",
            **floors,
            "maxHeartRateUsed": 193,
            "restingHeartRateUsed": 47,
            "lactateThresholdHeartRateUsed": None,
        },
        {"sport": "RUNNING", "trainingMethod": "LACTATE_THRESHOLD", **floors, "lactateThresholdHeartRateUsed": 169},
    ]


def _seed_thresholds(sessions: sessionmaker[Session]) -> None:
    run_a, run_b, ride_c, ride_d = THRESHOLD_IDS
    older = [_activity(run_a, "running", "2026-10-01", lactateThresholdBpm=LTHR_RUN_OLD)]
    newer = [
        _activity(
            run_a, "running", "2026-10-01", lactateThresholdBpm=LTHR_RUN, getlactateThresholdSpeed=SPEED_CM_PER_MS
        ),
        _activity(run_b, "running", "2026-10-03", lactateThresholdBpm=LTHR_RUN, getlactateThresholdSpeed=SPEED_M_PER_S),
        _activity(
            ride_c,
            "road_biking",
            "2026-10-02",
            lactateThresholdBpm=LTHR_RIDE,
            maxFtp=FTP,
            trainingStressScore=TSS,
            intensityFactor=IF_FRACTION,
        ),
        _activity(ride_d, "road_biking", "2026-10-04", intensityFactor=IF_PERCENT),
    ]
    zones = _zones_payload()
    with sessions.begin() as session:
        athlete = ensure_athlete(session)
        save_raw_payload(session, athlete, ACTIVITIES, "2026-10-01..2026-10-01", 200, older, T0 - timedelta(days=1))
        raw_id = save_raw_payload(session, athlete, ACTIVITIES, "2026-10-01..2026-10-04", 200, newer, T0)
        upsert_activities(session, athlete, parse_activities(newer).records, raw_id)
        zones_id = save_raw_payload(session, athlete, HR_ZONES, HR_ZONES_REQUEST_KEY, 200, zones, T0)
        save_hr_zones(session, athlete, parse_hr_zones(zones).records, zones_id, TODAY)
        set_manual(session, athlete, "ftp", "ride", date(2026, 10, 2), MANUAL_FTP)
        rebuild_derived(session, athlete)


def test_report_threshold_coverage(app_sessions: sessionmaker[Session]) -> None:
    _seed_thresholds(app_sessions)
    with app_sessions() as session:
        report = _load_script().collect(session, TODAY)["thresholds"]

    assert report["raw_activities"] == 4
    fields = report["fields"]
    # The older version of the first run is ignored, so runs carry one distinct LTHR.
    assert fields["lactateThresholdBpm"] == {
        "present": 3,
        "not_a_number": 0,
        "per_group": {"run": 2, "ride": 1},
        "distinct_values_per_group": {"run": 1, "ride": 1},
    }
    assert fields["getlactateThresholdSpeed"]["distinct_values_per_group"] == {"run": 2}
    assert fields["maxFtp"]["per_group"] == {"ride": 1}
    assert fields["intensityFactor"]["per_group"] == {"ride": 2}
    assert report["threshold_speed_unit"] == {"plausible_as_cm_per_ms_only": 1, "plausible_as_m_per_s_only": 1}
    assert report["intensity_factor_scale"] == {"fraction_0_to_3": 1, "percent_3_to_300": 1}
    assert report["threshold_like_keys"] == {
        "getlactateThresholdSpeed": 2,
        "intensityFactor": 2,
        "lactateThresholdBpm": 3,
        "maxFtp": 1,
        "trainingStressScore": 1,
    }
    # The m/s value is ten times too large after the cm/ms conversion, so it isn't stored.
    assert report["stored_columns_per_group"] == {
        "lactate_threshold_hr": {"run": 2, "ride": 1},
        "threshold_speed_mps": {"run": 1},
        "ftp_w": {"ride": 1},
        "garmin_tss": {"ride": 1},
        "garmin_if": {"ride": 1},
    }
    zones = report["zones"]
    assert zones["raw_responses"] == 1
    assert zones["newest_response_items"] == 2
    assert zones["newest_response_keys"]["lactateThresholdHeartRateUsed"] == 1
    assert zones["newest_response_keys"]["maxHeartRateUsed"] == 1
    assert zones["history_per_sport"] == {
        "DEFAULT": {
            "rows": 1,
            "methods": {"HR_MAX": 1},
            "with_all_floors": 1,
            "with_max_hr": 1,
            "with_resting_hr": 1,
            "with_lthr": 0,
        },
        "RUNNING": {
            "rows": 1,
            "methods": {"LACTATE_THRESHOLD": 1},
            "with_all_floors": 1,
            "with_max_hr": 0,
            "with_resting_hr": 0,
            "with_lthr": 1,
        },
    }
    timeline = report["timeline_rows"]
    assert timeline["manual"] == {"ftp": {"ride": 1}}
    assert timeline["garmin_zones"] == {"lthr": {"run": 1}, "max_hr": {"all": 1}, "resting_hr": {"all": 1}}
    assert timeline["activity"] == {"ftp": {"ride": 1}, "lthr": {"ride": 1, "run": 1}, "threshold_speed": {"run": 1}}


def test_threshold_coverage_holds_no_values_or_ids(app_sessions: sessionmaker[Session]) -> None:
    _seed_thresholds(app_sessions)
    with app_sessions() as session:
        output = json.dumps(_load_script().collect(session, TODAY)["thresholds"])

    values = (
        LTHR_RUN,
        LTHR_RUN_OLD,
        LTHR_RIDE,
        SPEED_CM_PER_MS,
        SPEED_M_PER_S,
        FTP,
        MANUAL_FTP,
        TSS,
        IF_FRACTION,
        193,
        169,
    )
    for forbidden in (*(str(v) for v in values), "3.417", "2026-10", *(str(i) for i in THRESHOLD_IDS)):
        assert forbidden not in output
    assert ": 47" not in output and ": 83" not in output


def test_threshold_coverage_on_an_empty_database(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions() as session:
        report = _load_script().collect(session, TODAY)["thresholds"]
    assert report["raw_activities"] == 0
    assert report["fields"]["maxFtp"] == {
        "present": 0,
        "not_a_number": 0,
        "per_group": {},
        "distinct_values_per_group": {},
    }
    assert report["threshold_speed_unit"] == {}
    assert report["zones"] == {
        "raw_responses": 0,
        "newest_response_items": 0,
        "newest_response_keys": {},
        "history_per_sport": {},
    }
    assert report["timeline_rows"] == {}
