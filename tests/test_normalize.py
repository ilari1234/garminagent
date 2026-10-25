from datetime import UTC, date, datetime
from typing import Any

from garminagent.normalize import parse_activities, parse_daily_summaries
from tests.garmin.fakes import load_fixture


def test_activities_from_fixture() -> None:
    result = parse_activities(load_fixture("activities_search.json"))
    assert result.skipped == 0
    assert len(result.records) == 5
    run, ride = result.records[0], result.records[1]
    assert run.activity_id == 1000000001
    assert run.start_time_utc == datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
    assert run.start_time_local == datetime(2026, 10, 1, 7, 0)
    assert run.sport == "running"
    assert run.training_load == 95.0
    assert run.hr_zone_2_s == 2400.0
    assert ride.sport == "gravel_cycling"
    assert ride.avg_power_w == 185.0
    assert ride.normalized_power_w == 205.0


def test_activity_records_carry_no_location_or_free_text() -> None:
    record = parse_activities(load_fixture("activities_search.json")).records[0]
    fields = set(record.__dataclass_fields__)
    assert not {f for f in fields if any(word in f for word in ("lat", "lon", "name", "description"))}


def test_invalid_activities_are_skipped_not_fatal() -> None:
    items: list[Any] = [
        {"activityId": 1, "startTimeGMT": "2026-10-01 04:00:00"},
        {"activityId": "not-a-number", "startTimeGMT": "2026-10-01 04:00:00"},
        {"activityId": 2},
        {"activityId": 3, "startTimeGMT": "yesterday"},
        "not an object",
    ]
    result = parse_activities(items)
    assert [r.activity_id for r in result.records] == [1]
    assert result.skipped == 4


def test_activity_time_with_offset_is_converted_to_utc() -> None:
    (record,) = parse_activities([{"activityId": 1, "startTimeGMT": "2026-10-01T06:00:00+02:00"}]).records
    assert record.start_time_utc == datetime(2026, 10, 1, 4, 0, tzinfo=UTC)


def test_non_list_activity_input_gives_nothing() -> None:
    assert parse_activities({"unexpected": "shape"}).records == []
    assert parse_activities(None).records == []


def test_daily_summaries_from_fixture() -> None:
    result = parse_daily_summaries(load_fixture("daily_summaries.json"))
    assert result.skipped == 0
    first, second = result.records
    assert first.calendar_date == date(2026, 10, 1)
    assert first.resting_hr == 47
    assert first.steps == 12000
    assert first.stress_avg == 28
    assert first.body_battery_charged == 60
    assert first.intensity_vigorous_min == 35
    assert first.calories_total == 2500
    assert second.stress_avg is None  # absent in the payload, not an error
    assert second.steps == 6500


def test_daily_steps_accepts_bare_number() -> None:
    (record,) = parse_daily_summaries({"data": [{"calendarDate": "2026-10-01", "movement": {"steps": 42}}]}).records
    assert record.steps == 42


def test_invalid_daily_summaries_are_skipped() -> None:
    payload: dict[str, Any] = {
        "data": [{"calendarDate": "not-a-date"}, {"heartRate": {}}, {"calendarDate": "2026-10-03"}]
    }
    result = parse_daily_summaries(payload)
    assert [r.calendar_date for r in result.records] == [date(2026, 10, 3)]
    assert result.skipped == 2


def test_daily_summaries_unexpected_shape() -> None:
    assert parse_daily_summaries([]).records == []
    assert parse_daily_summaries({"data": "nope"}).records == []
