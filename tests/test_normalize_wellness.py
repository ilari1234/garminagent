"""Sleep, HRV, weight and the daily-summary stress/Body Battery additions (synthetic, spec-shaped)."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from garminagent.normalize import parse_daily_summaries, parse_hrv, parse_sleep, parse_weight

DAY = date(2026, 10, 1)


def sleep_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "calendarDate": "2026-10-01",
        "sleepStartTimestampGMT": "2026-09-30T21:30:00",
        "sleepEndTimestampGMT": "2026-10-01T05:30:00",
        "sleepTimeSeconds": 27000,
        "deepSleepSeconds": 5400,
        "lightSleepSeconds": 14400,
        "remSleepSeconds": 7200,
        "awakeSleepSeconds": 1800,
        "napTimeSeconds": 0,
        "awakeCount": 2,
        "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}, "deepPercentage": {"value": 20}},
        "avgHeartRate": 48.0,
        "avgSleepStress": 14.0,
        "averageRespirationValue": 13.5,
        "averageSpO2Value": 95.0,
        "userNote": "free text that must never be stored",
        "sleepScoreFeedback": "POSITIVE_LONG_AND_DEEP",
    }
    return {**item, **overrides}


def test_sleep_summary() -> None:
    (record,) = parse_sleep([sleep_item()]).records
    assert record.calendar_date == DAY
    assert record.sleep_start_utc == datetime(2026, 9, 30, 21, 30, tzinfo=UTC)
    assert (record.sleep_s, record.deep_s, record.light_s, record.rem_s, record.awake_s) == (
        27000,
        5400,
        14400,
        7200,
        1800,
    )
    assert (record.sleep_score, record.awake_count, record.avg_hr) == (82, 2, 48.0)
    assert not any("note" in f or "feedback" in f or "insight" in f for f in record.__dataclass_fields__)


def test_sleep_accepts_epoch_millisecond_timestamps() -> None:
    (record,) = parse_sleep([sleep_item(sleepStartTimestampGMT=1_790_000_000_000)]).records
    assert record.sleep_start_utc == datetime.fromtimestamp(1_790_000_000, UTC)


@pytest.mark.parametrize(
    ("field", "value", "column"),
    [
        ("sleepTimeSeconds", 999_999, "sleep_s"),
        ("avgHeartRate", 5.0, "avg_hr"),
        ("averageSpO2Value", 140.0, "avg_spo2"),
        ("sleepStartTimestampGMT", "not a time", "sleep_start_utc"),
    ],
)
def test_sleep_implausible_values_become_none(field: str, value: Any, column: str) -> None:
    (record,) = parse_sleep([sleep_item(**{field: value})]).records
    assert getattr(record, column) is None


def test_sleep_without_scores_and_invalid_items() -> None:
    result = parse_sleep([sleep_item(sleepScores=None), {"sleepTimeSeconds": 1}, "nope"])
    assert [r.sleep_score for r in result.records] == [None]
    assert result.skipped == 2


def test_hrv_summaries() -> None:
    payload = {
        "userProfilePk": 1,
        "hrvSummaries": [
            {
                "calendarDate": "2026-10-01",
                "weeklyAvg": 62,
                "lastNightAvg": 58,
                "lastNight5MinHigh": 81,
                "baseline": {"lowUpper": 50, "balancedLow": 55, "balancedUpper": 70, "markerValue": 0.4},
                "status": "BALANCED",
                "feedbackPhrase": "HRV_BALANCED_2",
            },
            {"calendarDate": "2026-10-02", "lastNightAvg": 999, "status": "free text status!"},
        ],
    }
    first, second = parse_hrv(payload).records
    assert (first.last_night_avg, first.last_night_5min_high, first.weekly_avg) == (58, 81, 62)
    assert (first.baseline_low_upper, first.baseline_balanced_low, first.baseline_balanced_upper) == (50, 55, 70)
    assert first.status == "BALANCED"
    assert second.last_night_avg is None  # implausible
    assert second.status is None  # not an enum code


def test_hrv_unexpected_shape() -> None:
    assert parse_hrv([]).records == []
    assert parse_hrv({"hrvSummaries": "nope"}).records == []


def weight_day(**latest: Any) -> dict[str, Any]:
    return {"summaryDate": "2026-10-01", "numOfWeightEntries": 1, "latestWeight": latest}


@pytest.mark.parametrize(("raw", "kg"), [(72.4, 72.4), (72400.0, 72.4), (5.0, None), (900_000.0, None)])
def test_weight_units(raw: float, kg: float | None) -> None:
    (record,) = parse_weight({"dailyWeightSummaries": [weight_day(weight=raw)]}).records
    assert record.weight_kg == (pytest.approx(kg) if kg is not None else None)


def test_weight_body_composition_and_fallback_to_all_metrics() -> None:
    payload = {
        "dailyWeightSummaries": [
            weight_day(weight=72.4, bmi=22.1, bodyFat=14.5),
            {"summaryDate": "2026-10-02", "allWeightMetrics": [{"weight": 72.0}, {"weight": 71.8, "bodyFat": 99}]},
        ]
    }
    first, second = parse_weight(payload).records
    assert (first.bmi, first.body_fat_pct, first.entries) == (22.1, 14.5, 1)
    assert second.weight_kg == pytest.approx(71.8)
    assert second.body_fat_pct is None  # implausible


def test_daily_summary_stress_durations_and_end_of_day_battery() -> None:
    payload = {
        "data": [
            {
                "calendarDate": "2026-10-01",
                "stress": {
                    "avgLevel": 30,
                    "restDurationInMillis": 7 * 3_600_000,
                    "lowStressDurationInMillis": 5 * 3_600_000,
                    "mediumStressDurationInMillis": 90 * 60_000,
                    "highStressDurationInMillis": 20 * 60_000,
                },
                "bodyBattery": {"minValue": 20, "endOfDayValue": 35},
            }
        ]
    }
    (record,) = parse_daily_summaries(payload).records
    assert (record.stress_rest_min, record.stress_low_min, record.stress_medium_min, record.stress_high_min) == (
        420,
        300,
        90,
        20,
    )
    assert record.body_battery_end_of_day == 35
