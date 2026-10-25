"""Raw Garmin JSON -> normalized records.

Lenient by design: every field is optional except the ones a row can't exist without (IDs,
dates), unknown fields are ignored, and a record that still fails validation is skipped and
counted rather than failing the whole batch. Field names follow the Personal API spec.

Only training-relevant values are kept: no GPS coordinates, no names or descriptions.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, ValidationError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivityRecord:
    activity_id: int
    start_time_utc: datetime
    start_time_local: datetime | None = None
    sport: str | None = None
    event_type: str | None = None
    duration_s: float | None = None
    moving_duration_s: float | None = None
    elapsed_duration_s: float | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_power_w: float | None = None
    normalized_power_w: float | None = None
    aerobic_te: float | None = None
    anaerobic_te: float | None = None
    training_effect_label: str | None = None
    training_load: float | None = None
    vo2max: float | None = None
    hr_zone_1_s: float | None = None
    hr_zone_2_s: float | None = None
    hr_zone_3_s: float | None = None
    hr_zone_4_s: float | None = None
    hr_zone_5_s: float | None = None


@dataclass(frozen=True)
class DailySummaryRecord:
    calendar_date: date
    resting_hr: int | None = None
    min_hr: int | None = None
    max_hr: int | None = None
    steps: int | None = None
    stress_avg: int | None = None
    stress_max: int | None = None
    body_battery_min: int | None = None
    body_battery_max: int | None = None
    body_battery_charged: int | None = None
    body_battery_drained: int | None = None
    respiration_avg: float | None = None
    intensity_moderate_min: int | None = None
    intensity_vigorous_min: int | None = None
    calories_total: int | None = None
    calories_active: int | None = None


@dataclass
class Normalized[T]:
    records: list[T] = field(default_factory=list[T])
    skipped: int = 0


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- activities (activity search items; the spec's SummarizedActivityDTO) ---


class _TypeRef(_Lenient):
    typeKey: str | None = None


class _ActivityIn(_Lenient):
    activityId: int
    startTimeGMT: str
    startTimeLocal: str | None = None
    activityType: _TypeRef | None = None
    eventType: _TypeRef | None = None
    duration: float | None = None
    movingDuration: float | None = None
    elapsedDuration: float | None = None
    distance: float | None = None
    elevationGain: float | None = None
    elevationLoss: float | None = None
    averageHR: float | None = None
    maxHR: float | None = None
    avgPower: float | None = None
    normPower: float | None = None
    aerobicTrainingEffect: float | None = None
    anaerobicTrainingEffect: float | None = None
    trainingEffectLabel: str | None = None
    activityTrainingLoad: float | None = None
    vO2MaxValue: float | None = None
    hrTimeInZone_1: float | None = None
    hrTimeInZone_2: float | None = None
    hrTimeInZone_3: float | None = None
    hrTimeInZone_4: float | None = None
    hrTimeInZone_5: float | None = None


def _parse_time(value: str) -> datetime:
    # Garmin uses "YYYY-MM-DD HH:MM:SS", sometimes with a fraction; fromisoformat takes both.
    return datetime.fromisoformat(value.strip())


def _as_list(value: object) -> list[object]:
    return cast(list[object], value) if isinstance(value, list) else []


def parse_activities(items: Any) -> Normalized[ActivityRecord]:
    """Activity search results (a list of summaries)."""
    result = Normalized[ActivityRecord]()
    for item in _as_list(items):
        try:
            raw = _ActivityIn.model_validate(item)
            start_gmt = _parse_time(raw.startTimeGMT)
            start_utc = start_gmt.replace(tzinfo=UTC) if start_gmt.tzinfo is None else start_gmt.astimezone(UTC)
            start_local = _parse_time(raw.startTimeLocal) if raw.startTimeLocal else None
        except ValidationError, ValueError, TypeError:
            result.skipped += 1
            continue
        result.records.append(
            ActivityRecord(
                activity_id=raw.activityId,
                start_time_utc=start_utc,
                start_time_local=start_local,
                sport=raw.activityType.typeKey if raw.activityType else None,
                event_type=raw.eventType.typeKey if raw.eventType else None,
                duration_s=raw.duration,
                moving_duration_s=raw.movingDuration,
                elapsed_duration_s=raw.elapsedDuration,
                distance_m=raw.distance,
                elevation_gain_m=raw.elevationGain,
                elevation_loss_m=raw.elevationLoss,
                avg_hr=raw.averageHR,
                max_hr=raw.maxHR,
                avg_power_w=raw.avgPower,
                normalized_power_w=raw.normPower,
                aerobic_te=raw.aerobicTrainingEffect,
                anaerobic_te=raw.anaerobicTrainingEffect,
                training_effect_label=raw.trainingEffectLabel,
                training_load=raw.activityTrainingLoad,
                vo2max=raw.vO2MaxValue,
                hr_zone_1_s=raw.hrTimeInZone_1,
                hr_zone_2_s=raw.hrTimeInZone_2,
                hr_zone_3_s=raw.hrTimeInZone_3,
                hr_zone_4_s=raw.hrTimeInZone_4,
                hr_zone_5_s=raw.hrTimeInZone_5,
            )
        )
    if result.skipped:
        log.warning("skipped %d activity records that failed validation", result.skipped)
    return result


# --- daily summaries (v2/dailysummaries; the spec's UserDailySummaryV2DTO) ---


class _HeartRate(_Lenient):
    minValue: int | None = None
    maxValue: int | None = None
    restingValue: int | None = None


class _Stress(_Lenient):
    avgLevel: int | None = None
    maxLevel: int | None = None


class _BodyBattery(_Lenient):
    minValue: int | None = None
    maxValue: int | None = None
    chargedValue: int | None = None
    drainedValue: int | None = None


class _Respiration(_Lenient):
    avgValue: float | None = None


class _Intensity(_Lenient):
    moderate: int | None = None
    vigorous: int | None = None


class _Calories(_Lenient):
    burnedTotal: int | None = None
    burnedActive: int | None = None


class _MovementValue(_Lenient):
    value: int | None = None


class _Movement(_Lenient):
    steps: _MovementValue | int | None = None


class _DailyIn(_Lenient):
    calendarDate: date
    heartRate: _HeartRate | None = None
    stress: _Stress | None = None
    bodyBattery: _BodyBattery | None = None
    respiration: _Respiration | None = None
    intensityMinutes: _Intensity | None = None
    calories: _Calories | None = None
    movement: _Movement | None = None


def _steps(movement: _Movement | None) -> int | None:
    if movement is None or movement.steps is None:
        return None
    # The spec nests the count ({"value": n}); accept a bare number too.
    return movement.steps if isinstance(movement.steps, int) else movement.steps.value


def parse_daily_summaries(payload: Any) -> Normalized[DailySummaryRecord]:
    result = Normalized[DailySummaryRecord]()
    data = cast(dict[str, object], payload).get("data") if isinstance(payload, dict) else None
    for item in _as_list(data):
        try:
            raw = _DailyIn.model_validate(item)
        except ValidationError:
            result.skipped += 1
            continue
        hr, stress, bb = raw.heartRate, raw.stress, raw.bodyBattery
        result.records.append(
            DailySummaryRecord(
                calendar_date=raw.calendarDate,
                resting_hr=hr.restingValue if hr else None,
                min_hr=hr.minValue if hr else None,
                max_hr=hr.maxValue if hr else None,
                steps=_steps(raw.movement),
                stress_avg=stress.avgLevel if stress else None,
                stress_max=stress.maxLevel if stress else None,
                body_battery_min=bb.minValue if bb else None,
                body_battery_max=bb.maxValue if bb else None,
                body_battery_charged=bb.chargedValue if bb else None,
                body_battery_drained=bb.drainedValue if bb else None,
                respiration_avg=raw.respiration.avgValue if raw.respiration else None,
                intensity_moderate_min=raw.intensityMinutes.moderate if raw.intensityMinutes else None,
                intensity_vigorous_min=raw.intensityMinutes.vigorous if raw.intensityMinutes else None,
                calories_total=raw.calories.burnedTotal if raw.calories else None,
                calories_active=raw.calories.burnedActive if raw.calories else None,
            )
        )
    if result.skipped:
        log.warning("skipped %d daily summary records that failed validation", result.skipped)
    return result
