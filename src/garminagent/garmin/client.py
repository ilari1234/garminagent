"""Typed access to all 26 Garmin Connect Personal API endpoints.

Methods return the decoded JSON exactly as Garmin sent it (None for 204 No Content), so the
raw store keeps the original payload; mapping to the normalized schema happens downstream.

Range endpoints check their span against RANGE_LIMITS before sending anything. Callers that
need longer spans split them with `date_chunks`.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Self, cast

from garminagent.config import Settings
from garminagent.garmin.errors import GarminAuthError, GarminError
from garminagent.garmin.files import ActivityFile, parse_activity_file
from garminagent.garmin.transport import GarminTransport, QueryParams

log = logging.getLogger(__name__)

type Json = dict[str, Json] | list[Json] | str | int | float | bool | None

# Maximum span per request in days, inclusive of both ends. Documented limits where Garmin
# gives one (stress/steps 28 enforced, sleep 31, daily summaries 1 year); otherwise the span
# measured to work in M0, since the real limit is unknown.
RANGE_LIMITS = {
    "sleep": 31,
    "hydration": 31,
    "body_battery": 31,
    "steps": 28,
    "stress": 28,
    "hrv": 28,
    "weight": 28,
    "weight_goals": 28,
    "wellness_activities": 28,
    "daily_summaries": 365,
}
ACTIVITY_PAGE_SIZE = 99  # Garmin's default and documented page size
KEY_EXPIRY_WARNING_DAYS = 14


def date_chunks(start: date, end: date, max_days: int) -> Iterator[tuple[date, date]]:
    """Split [start, end] into consecutive inclusive spans of at most max_days."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(days=max_days - 1))
        yield chunk_start, chunk_end
        chunk_start = chunk_end + timedelta(days=1)


def _check_range(kind: str, start: date, end: date) -> None:
    if end < start:
        raise ValueError(f"{kind}: end {end} is before start {start}")
    days = (end - start).days + 1
    if days > RANGE_LIMITS[kind]:
        raise ValueError(f"{kind}: {days} days exceeds the {RANGE_LIMITS[kind]}-day limit per request")


class GarminClient(GarminTransport):
    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if settings.garmin_personal_api_key is None:
            raise GarminAuthError("GARMIN_PERSONAL_API_KEY is not set")
        expires = settings.garmin_api_key_expires
        if expires is not None:
            days_left = (expires - datetime.now(UTC).date()).days
            if days_left < 0:
                log.error("Garmin API key expired on %s; create a new one in Garmin Connect", expires)
            elif days_left <= KEY_EXPIRY_WARNING_DAYS:
                log.warning("Garmin API key expires in %d days (%s)", days_left, expires)
        return cls(
            settings.garmin_personal_api_key,
            base_url=settings.garmin_base_url,
            min_interval=settings.garmin_min_request_interval,
            max_retries=settings.garmin_max_retries,
        )

    def get_json(self, path: str, params: QueryParams | None = None) -> Json:
        resp = self.request("GET", path, params)
        if resp.status_code == 204 or not resp.content:
            return None
        return cast(Json, resp.json())

    # --- profile ---

    def get_user_settings(self) -> Json:
        return self.get_json("/userprofile-service/userprofile/settings")

    def get_heart_rate_zones(self, *, generate_defaults: bool | None = None) -> Json:
        return self.get_json("/biometric-service/heartRateZones/v2", {"generateDefaults": generate_defaults})

    def get_fitness_age(self, day: date, *, full_history_scan: bool | None = None) -> Json:
        return self.get_json(f"/fitnessage-service/fitnessage/v2/{day}", {"fullHistoryScan": full_history_scan})

    # --- activities ---

    def search_activities(
        self,
        *,
        start: int = 0,
        limit: int = ACTIVITY_PAGE_SIZE,
        start_date: date | None = None,
        end_date: date | None = None,
        sort_order: str | None = None,
        activity_type: str | None = None,
        exclude_children: bool | None = None,
    ) -> Json:
        return self.get_json(
            "/activitylist-service/activities/search/activities",
            {
                "start": start,
                "limit": limit,
                "startDate": start_date.isoformat() if start_date else None,
                "endDate": end_date.isoformat() if end_date else None,
                "sortOrder": sort_order,
                "activityType": activity_type,
                "excludeChildren": exclude_children,
            },
        )

    def iter_activities(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        sort_order: str = "asc",
        page_size: int = ACTIVITY_PAGE_SIZE,
    ) -> Iterator[dict[str, Json]]:
        """All activity summaries in the date range, paging until a short page."""
        offset = 0
        while True:
            page = self.search_activities(
                start=offset, limit=page_size, start_date=start_date, end_date=end_date, sort_order=sort_order
            )
            if page is None:
                return
            if not isinstance(page, list):
                raise GarminError("activity search returned an unexpected shape (expected a list)")
            for item in page:
                if isinstance(item, dict):
                    yield item
            if len(page) < page_size:
                return
            offset += page_size

    def get_activity(self, activity_id: int) -> Json:
        return self.get_json(f"/activity-service/activity/{activity_id}")

    def get_activity_workouts(self, activity_id: int) -> Json:
        return self.get_json(f"/activity-service/activity/{activity_id}/workouts")

    def download_activity_file(self, activity_id: int) -> ActivityFile:
        resp = self.request("GET", f"/download-service/files/activity/{activity_id}")
        return parse_activity_file(activity_id, resp.content)

    # --- recovery and wellness ---

    def get_hrv(self, day: date) -> Json:
        return self.get_json(f"/hrv-service/hrv/{day}")

    def get_hrv_range(self, start: date, end: date) -> Json:
        _check_range("hrv", start, end)
        return self.get_json(f"/hrv-service/hrv/daily/{start}/{end}")

    def get_sleep(self, day: date) -> Json:
        return self.get_json("/sleep-service/sleep/dailySleep", {"date": day.isoformat()})

    def get_sleep_range(self, start: date, end: date) -> Json:
        _check_range("sleep", start, end)
        return self.get_json(
            "/sleep-service/sleep/dailySleepSummariesByDate",
            {"startDate": start.isoformat(), "endDate": end.isoformat()},
        )

    def get_user_summary(self, day: date) -> Json:
        return self.get_json("/usersummary-service/usersummary/daily", {"calendarDate": day.isoformat()})

    def get_daily_summaries(self, start: date, end: date) -> Json:
        _check_range("daily_summaries", start, end)
        return self.get_json(
            "/usersummary-service/v2/dailysummaries", {"startDate": start.isoformat(), "endDate": end.isoformat()}
        )

    def get_steps_range(self, start: date, end: date) -> Json:
        _check_range("steps", start, end)
        return self.get_json(f"/usersummary-service/stats/steps/daily/{start}/{end}")

    def get_stress_range(self, start: date, end: date) -> Json:
        _check_range("stress", start, end)
        return self.get_json(f"/usersummary-service/stats/stress/daily/{start}/{end}")

    def get_hydration_range(self, start: date, end: date) -> Json:
        _check_range("hydration", start, end)
        return self.get_json(f"/usersummary-service/stats/hydration/daily/{start}/{end}")

    def get_body_battery_range(self, start: date, end: date, *, chunks: int | None = None) -> Json:
        _check_range("body_battery", start, end)
        return self.get_json(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            {"startDate": start.isoformat(), "endDate": end.isoformat(), "chunks": chunks},
        )

    def get_spo2(self, day: date) -> Json:
        return self.get_json(f"/wellness-service/wellness/daily/spo2acclimation/{day}")

    def get_daily_heart_rate(self, day: date) -> Json:
        return self.get_json("/wellness-service/wellness/dailyHeartRate", {"date": day.isoformat()})

    def get_wellness_activities(self, start: date, end: date) -> Json:
        _check_range("wellness_activities", start, end)
        return self.get_json(f"/wellnessactivity-service/activity/summary/range/{start}/{end}")

    # --- body, nutrition, cycle ---

    def get_weight_range(self, start: date, end: date, *, include_all: bool | None = None) -> Json:
        _check_range("weight", start, end)
        return self.get_json(f"/weight-service/weight/range/{start}/{end}", {"includeAll": include_all})

    def get_weight_entries(self, start: date, end: date) -> Json:
        """All weight entries. Returned HTTP 400 for a 28-day span in M0; prefer
        `get_weight_range(include_all=True)`."""
        _check_range("weight", start, end)
        return self.get_json(f"/weight-service/weight/all/{start}/{end}")

    def get_weight_goals(self, start: date, end: date) -> Json:
        _check_range("weight_goals", start, end)
        return self.get_json(f"/goal-service/goal/user/effective/weightgoal/{start}/{end}")

    def get_nutrition(self, day: date) -> Json:
        return self.get_json(f"/nutrition-service/food/logs/{day}")

    def get_menstrual_summary(self, day: date) -> Json:
        return self.get_json(f"/periodichealth-service/menstrualcycle/summary/{day}")

    def get_menstrual_day_view(self, day: date) -> Json:
        return self.get_json(f"/periodichealth-service/menstrualcycle/dayview/{day}")
