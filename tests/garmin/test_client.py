import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from garminagent.config import Settings
from garminagent.garmin import RANGE_LIMITS, GarminAuthError, GarminClient, UnexpectedFileError, date_chunks
from tests.garmin.fakes import FakeGarmin, load_fixture, make_fit, make_zip

D = date(2026, 10, 1)
E = date(2026, 10, 14)

ENDPOINTS: list[tuple[str, Callable[[GarminClient], Any], str, dict[str, str]]] = [
    ("user settings", lambda c: c.get_user_settings(), "/userprofile-service/userprofile/settings", {}),
    ("hr zones", lambda c: c.get_heart_rate_zones(), "/biometric-service/heartRateZones/v2", {}),
    (
        "fitness age",
        lambda c: c.get_fitness_age(D, full_history_scan=False),
        "/fitnessage-service/fitnessage/v2/2026-10-01",
        {"fullHistoryScan": "false"},
    ),
    (
        "activity search",
        lambda c: c.search_activities(start=99, limit=10, start_date=D, end_date=E, sort_order="asc"),
        "/activitylist-service/activities/search/activities",
        {"start": "99", "limit": "10", "startDate": "2026-10-01", "endDate": "2026-10-14", "sortOrder": "asc"},
    ),
    ("activity", lambda c: c.get_activity(42), "/activity-service/activity/42", {}),
    ("activity workouts", lambda c: c.get_activity_workouts(42), "/activity-service/activity/42/workouts", {}),
    ("hrv day", lambda c: c.get_hrv(D), "/hrv-service/hrv/2026-10-01", {}),
    ("hrv range", lambda c: c.get_hrv_range(D, E), "/hrv-service/hrv/daily/2026-10-01/2026-10-14", {}),
    ("sleep day", lambda c: c.get_sleep(D), "/sleep-service/sleep/dailySleep", {"date": "2026-10-01"}),
    (
        "sleep range",
        lambda c: c.get_sleep_range(D, E),
        "/sleep-service/sleep/dailySleepSummariesByDate",
        {"startDate": "2026-10-01", "endDate": "2026-10-14"},
    ),
    (
        "user summary",
        lambda c: c.get_user_summary(D),
        "/usersummary-service/usersummary/daily",
        {"calendarDate": "2026-10-01"},
    ),
    (
        "daily summaries",
        lambda c: c.get_daily_summaries(D, E),
        "/usersummary-service/v2/dailysummaries",
        {"startDate": "2026-10-01", "endDate": "2026-10-14"},
    ),
    (
        "steps",
        lambda c: c.get_steps_range(D, E),
        "/usersummary-service/stats/steps/daily/2026-10-01/2026-10-14",
        {},
    ),
    (
        "stress",
        lambda c: c.get_stress_range(D, E),
        "/usersummary-service/stats/stress/daily/2026-10-01/2026-10-14",
        {},
    ),
    (
        "hydration",
        lambda c: c.get_hydration_range(D, E),
        "/usersummary-service/stats/hydration/daily/2026-10-01/2026-10-14",
        {},
    ),
    (
        "body battery",
        lambda c: c.get_body_battery_range(D, E, chunks=4),
        "/wellness-service/wellness/bodyBattery/reports/daily",
        {"startDate": "2026-10-01", "endDate": "2026-10-14", "chunks": "4"},
    ),
    ("spo2", lambda c: c.get_spo2(D), "/wellness-service/wellness/daily/spo2acclimation/2026-10-01", {}),
    (
        "daily heart rate",
        lambda c: c.get_daily_heart_rate(D),
        "/wellness-service/wellness/dailyHeartRate",
        {"date": "2026-10-01"},
    ),
    (
        "wellness activities",
        lambda c: c.get_wellness_activities(D, E),
        "/wellnessactivity-service/activity/summary/range/2026-10-01/2026-10-14",
        {},
    ),
    (
        "weight range",
        lambda c: c.get_weight_range(D, E, include_all=True),
        "/weight-service/weight/range/2026-10-01/2026-10-14",
        {"includeAll": "true"},
    ),
    ("weight entries", lambda c: c.get_weight_entries(D, E), "/weight-service/weight/all/2026-10-01/2026-10-14", {}),
    (
        "weight goals",
        lambda c: c.get_weight_goals(D, E),
        "/goal-service/goal/user/effective/weightgoal/2026-10-01/2026-10-14",
        {},
    ),
    ("nutrition", lambda c: c.get_nutrition(D), "/nutrition-service/food/logs/2026-10-01", {}),
    (
        "menstrual summary",
        lambda c: c.get_menstrual_summary(D),
        "/periodichealth-service/menstrualcycle/summary/2026-10-01",
        {},
    ),
    (
        "menstrual day view",
        lambda c: c.get_menstrual_day_view(D),
        "/periodichealth-service/menstrualcycle/dayview/2026-10-01",
        {},
    ),
]


@pytest.mark.parametrize(("name", "call", "path", "params"), ENDPOINTS, ids=[e[0] for e in ENDPOINTS])
def test_endpoint_requests(
    garmin: FakeGarmin, name: str, call: Callable[[GarminClient], Any], path: str, params: dict[str, str]
) -> None:
    garmin.json(path, {"fake": name})
    with garmin.client(min_interval=0) as client:
        assert call(client) == {"fake": name}
    (seen,) = garmin.requests_to(path)
    assert seen.method == "GET"
    assert seen.params == params


def test_endpoint_table_covers_the_spec() -> None:
    # 25 JSON endpoints in the table + the activity file download = 26 in the spec.
    assert len(ENDPOINTS) + 1 == 26


def test_payloads_are_returned_unchanged(garmin: FakeGarmin) -> None:
    fixture = load_fixture("daily_summaries.json")
    garmin.json("/usersummary-service/v2/dailysummaries", fixture)
    with garmin.client(min_interval=0) as client:
        assert client.get_daily_summaries(D, E) == fixture


def test_no_content_returns_none(garmin: FakeGarmin) -> None:
    garmin.reply("GET", "/periodichealth-service/menstrualcycle/summary/2026-10-01", httpx.Response(204))
    with garmin.client(min_interval=0) as client:
        assert client.get_menstrual_summary(D) is None


@pytest.mark.parametrize("kind", ["sleep", "stress", "daily_summaries"])
def test_range_limits_are_enforced_before_sending(garmin: FakeGarmin, kind: str) -> None:
    limit = RANGE_LIMITS[kind]
    at_limit = D + timedelta(days=limit - 1)
    calls: dict[str, tuple[Callable[[GarminClient, date, date], Any], str]] = {
        "sleep": (GarminClient.get_sleep_range, "/sleep-service/sleep/dailySleepSummariesByDate"),
        "stress": (GarminClient.get_stress_range, f"/usersummary-service/stats/stress/daily/{D}/{at_limit}"),
        "daily_summaries": (GarminClient.get_daily_summaries, "/usersummary-service/v2/dailysummaries"),
    }
    call, path = calls[kind]
    garmin.json(path, [])
    with garmin.client(min_interval=0) as client:
        call(client, D, at_limit)  # exactly at the limit: allowed
        with pytest.raises(ValueError, match="limit"):
            call(client, D, at_limit + timedelta(days=1))
    assert len([s for s in garmin.seen if s.method == "GET"]) == 1


def test_reversed_range_is_rejected(garmin: FakeGarmin) -> None:
    with garmin.client() as client, pytest.raises(ValueError, match="before"):
        client.get_hrv_range(E, D)
    assert garmin.seen == []


def test_date_chunks_cover_range_without_gaps() -> None:
    chunks = list(date_chunks(date(2026, 1, 1), date(2026, 3, 11), 31))
    assert chunks == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 3, 3)),
        (date(2026, 3, 4), date(2026, 3, 11)),
    ]
    assert list(date_chunks(D, D, 28)) == [(D, D)]
    with pytest.raises(ValueError):
        list(date_chunks(E, D, 28))


def test_iter_activities_pages_until_short_page(garmin: FakeGarmin) -> None:
    items = load_fixture("activities_search.json")
    path = "/activitylist-service/activities/search/activities"
    garmin.reply(
        "GET",
        path,
        httpx.Response(200, json=items[0:2]),
        httpx.Response(200, json=items[2:4]),
        httpx.Response(200, json=items[4:5]),
    )
    with garmin.client(min_interval=0) as client:
        got = list(client.iter_activities(start_date=D, end_date=E, page_size=2))
    assert [a["activityId"] for a in got] == [a["activityId"] for a in items]
    assert [s.params["start"] for s in garmin.requests_to(path)] == ["0", "2", "4"]
    assert all(s.params["sortOrder"] == "asc" for s in garmin.requests_to(path))


def test_iter_activities_stops_on_empty_full_boundary(garmin: FakeGarmin) -> None:
    items = load_fixture("activities_search.json")[:2]
    path = "/activitylist-service/activities/search/activities"
    garmin.reply("GET", path, httpx.Response(200, json=items), httpx.Response(200, json=[]))
    with garmin.client(min_interval=0) as client:
        assert len(list(client.iter_activities(page_size=2))) == 2
    assert len(garmin.requests_to(path)) == 2


# --- activity files ---

FILE_PATH = "/download-service/files/activity/42"


def test_download_unzips_fit(garmin: FakeGarmin) -> None:
    fit = make_fit()
    original = make_zip({"42_ACTIVITY.fit": fit})
    garmin.reply("GET", FILE_PATH, httpx.Response(200, content=original))
    with garmin.client(min_interval=0) as client:
        result = client.download_activity_file(42)
    assert result.activity_id == 42
    assert result.original == original
    assert result.fit_files == {"42_ACTIVITY.fit": fit}


def test_download_accepts_bare_fit(garmin: FakeGarmin) -> None:
    fit = make_fit()
    garmin.reply("GET", FILE_PATH, httpx.Response(200, content=fit))
    with garmin.client(min_interval=0) as client:
        assert client.download_activity_file(42).fit_files == {"42.fit": fit}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"<html>not a file</html>", "neither a zip nor a FIT"),
        (make_zip({"42.gpx": b"<gpx/>"}), "no .fit member"),
        (make_zip({"42.fit": b"definitely not fit data"}), "lacks the FIT signature"),
    ],
    ids=["html", "zip-without-fit", "fit-without-signature"],
)
def test_download_rejects_unexpected_files(garmin: FakeGarmin, content: bytes, message: str) -> None:
    garmin.reply("GET", FILE_PATH, httpx.Response(200, content=content))
    with garmin.client(min_interval=0) as client, pytest.raises(UnexpectedFileError, match=message):
        client.download_activity_file(42)


# --- construction from settings ---


def test_from_settings_requires_key() -> None:
    with pytest.raises(GarminAuthError, match="not set"):
        GarminClient.from_settings(Settings())


def test_from_settings_warns_about_expiring_key(caplog: pytest.LogCaptureFixture) -> None:
    soon = datetime.now(UTC).date() + timedelta(days=3)
    settings = Settings(garmin_personal_api_key=SecretStr("k"), garmin_api_key_expires=soon)
    with caplog.at_level(logging.WARNING), GarminClient.from_settings(settings):
        pass
    assert "expires in 3 days" in caplog.text
