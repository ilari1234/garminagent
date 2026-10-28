"""Raw Garmin JSON -> normalized records.

Lenient by design: every field is optional except the ones a row can't exist without (IDs,
dates), unknown fields are ignored, and a record that still fails validation is skipped and
counted rather than failing the whole batch. Field names follow the Personal API spec.

Only training-relevant values are kept: no GPS coordinates, no names or descriptions.
"""

import logging
import re
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
    stress_rest_min: int | None = None
    stress_low_min: int | None = None
    stress_medium_min: int | None = None
    stress_high_min: int | None = None
    body_battery_end_of_day: int | None = None


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
    restDurationInMillis: int | None = None
    lowStressDurationInMillis: int | None = None
    mediumStressDurationInMillis: int | None = None
    highStressDurationInMillis: int | None = None


class _BodyBattery(_Lenient):
    minValue: int | None = None
    maxValue: int | None = None
    chargedValue: int | None = None
    drainedValue: int | None = None
    endOfDayValue: int | None = None


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
                stress_rest_min=_minutes(stress.restDurationInMillis) if stress else None,
                stress_low_min=_minutes(stress.lowStressDurationInMillis) if stress else None,
                stress_medium_min=_minutes(stress.mediumStressDurationInMillis) if stress else None,
                stress_high_min=_minutes(stress.highStressDurationInMillis) if stress else None,
                body_battery_end_of_day=bb.endOfDayValue if bb else None,
            )
        )
    if result.skipped:
        log.warning("skipped %d daily summary records that failed validation", result.skipped)
    return result


def _minutes(millis: int | None) -> int | None:
    return round(millis / 60_000) if millis is not None and 0 <= millis <= 86_400_000 else None


def _within(value: float | None, low: float, high: float) -> float | None:
    return value if value is not None and low <= value <= high else None


def _within_int(value: int | None, low: int, high: int) -> int | None:
    return value if value is not None and low <= value <= high else None


def _timestamp(value: Any) -> datetime | None:
    """ISO-8601 string (per the spec) or epoch milliseconds (as Garmin's APIs often send)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value / 1000, UTC)
        except OverflowError, OSError, ValueError:
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    return None


def _payload_list(payload: Any, key: str | None) -> list[object]:
    if key is None:
        return _as_list(payload)
    return _as_list(cast(dict[str, object], payload).get(key)) if isinstance(payload, dict) else []


# --- sleep (dailySleepSummariesByDate: a list of the spec's DailySleepDTO) ---

DAY_S = 86_400


@dataclass(frozen=True)
class SleepRecord:
    calendar_date: date
    sleep_start_utc: datetime | None = None
    sleep_end_utc: datetime | None = None
    sleep_s: int | None = None
    deep_s: int | None = None
    light_s: int | None = None
    rem_s: int | None = None
    awake_s: int | None = None
    nap_s: int | None = None
    awake_count: int | None = None
    sleep_score: int | None = None
    avg_hr: float | None = None
    avg_stress: float | None = None
    avg_respiration: float | None = None
    avg_spo2: float | None = None


class _ScoreItem(_Lenient):
    value: int | None = None


class _SleepIn(_Lenient):
    calendarDate: date
    sleepStartTimestampGMT: Any = None
    sleepEndTimestampGMT: Any = None
    sleepTimeSeconds: int | None = None
    deepSleepSeconds: int | None = None
    lightSleepSeconds: int | None = None
    remSleepSeconds: int | None = None
    awakeSleepSeconds: int | None = None
    napTimeSeconds: int | None = None
    awakeCount: int | None = None
    sleepScores: dict[str, _ScoreItem] | None = None
    avgHeartRate: float | None = None
    avgSleepStress: float | None = None
    averageRespirationValue: float | None = None
    averageSpO2Value: float | None = None


def parse_sleep(payload: Any) -> Normalized[SleepRecord]:
    """Sleep summaries; free text (notes, feedback, insights) is never read."""
    result = Normalized[SleepRecord]()
    for item in _payload_list(payload, None):
        try:
            raw = _SleepIn.model_validate(item)
        except ValidationError:
            result.skipped += 1
            continue
        overall = (raw.sleepScores or {}).get("overall")
        result.records.append(
            SleepRecord(
                calendar_date=raw.calendarDate,
                sleep_start_utc=_timestamp(raw.sleepStartTimestampGMT),
                sleep_end_utc=_timestamp(raw.sleepEndTimestampGMT),
                sleep_s=_within_int(raw.sleepTimeSeconds, 0, DAY_S),
                deep_s=_within_int(raw.deepSleepSeconds, 0, DAY_S),
                light_s=_within_int(raw.lightSleepSeconds, 0, DAY_S),
                rem_s=_within_int(raw.remSleepSeconds, 0, DAY_S),
                awake_s=_within_int(raw.awakeSleepSeconds, 0, DAY_S),
                nap_s=_within_int(raw.napTimeSeconds, 0, DAY_S),
                awake_count=_within_int(raw.awakeCount, 0, 500),
                sleep_score=_within_int(overall.value if overall else None, 0, 100),
                avg_hr=_within(raw.avgHeartRate, 20, 250),
                avg_stress=_within(raw.avgSleepStress, 0, 100),
                avg_respiration=_within(raw.averageRespirationValue, 1, 80),
                avg_spo2=_within(raw.averageSpO2Value, 50, 100),
            )
        )
    if result.skipped:
        log.warning("skipped %d sleep records that failed validation", result.skipped)
    return result


# --- HRV (hrv/daily: {"hrvSummaries": [the spec's HrvSummaryDTO, ...]}) ---

STATUS_CODE = re.compile(r"[A-Z][A-Z_]{0,31}")


@dataclass(frozen=True)
class HrvRecord:
    calendar_date: date
    last_night_avg: int | None = None
    last_night_5min_high: int | None = None
    weekly_avg: int | None = None
    baseline_low_upper: int | None = None
    baseline_balanced_low: int | None = None
    baseline_balanced_upper: int | None = None
    status: str | None = None


class _Baseline(_Lenient):
    lowUpper: int | None = None
    balancedLow: int | None = None
    balancedUpper: int | None = None


class _HrvIn(_Lenient):
    calendarDate: date
    lastNightAvg: int | None = None
    lastNight5MinHigh: int | None = None
    weeklyAvg: int | None = None
    baseline: _Baseline | None = None
    status: str | None = None


def _hrv_ms(value: int | None) -> int | None:
    return _within_int(value, 1, 300)


def parse_hrv(payload: Any) -> Normalized[HrvRecord]:
    result = Normalized[HrvRecord]()
    for item in _payload_list(payload, "hrvSummaries"):
        try:
            raw = _HrvIn.model_validate(item)
        except ValidationError:
            result.skipped += 1
            continue
        baseline = raw.baseline
        # Status is an enum code (BALANCED, LOW, ...); anything else is not kept.
        status = raw.status if raw.status and STATUS_CODE.fullmatch(raw.status) else None
        result.records.append(
            HrvRecord(
                calendar_date=raw.calendarDate,
                last_night_avg=_hrv_ms(raw.lastNightAvg),
                last_night_5min_high=_hrv_ms(raw.lastNight5MinHigh),
                weekly_avg=_hrv_ms(raw.weeklyAvg),
                baseline_low_upper=_hrv_ms(baseline.lowUpper) if baseline else None,
                baseline_balanced_low=_hrv_ms(baseline.balancedLow) if baseline else None,
                baseline_balanced_upper=_hrv_ms(baseline.balancedUpper) if baseline else None,
                status=status,
            )
        )
    if result.skipped:
        log.warning("skipped %d HRV records that failed validation", result.skipped)
    return result


# --- weight (weight/range: {"dailyWeightSummaries": [the spec's DailyWeightSummary, ...]}) ---


@dataclass(frozen=True)
class WeightRecord:
    calendar_date: date
    weight_kg: float | None = None
    bmi: float | None = None
    body_fat_pct: float | None = None
    entries: int | None = None


class _WeightIn(_Lenient):
    weight: float | None = None
    bmi: float | None = None
    bodyFat: float | None = None


class _WeightDayIn(_Lenient):
    summaryDate: date
    numOfWeightEntries: int | None = None
    latestWeight: _WeightIn | None = None
    allWeightMetrics: list[_WeightIn] | None = None


def _kilograms(value: float | None) -> float | None:
    # The spec says kilograms; Garmin's web APIs have used grams, so convert those.
    if value is None:
        return None
    kg = value / 1000 if value >= 1000 else value
    return _within(kg, 20, 300)


def parse_weight(payload: Any) -> Normalized[WeightRecord]:
    result = Normalized[WeightRecord]()
    for item in _payload_list(payload, "dailyWeightSummaries"):
        try:
            raw = _WeightDayIn.model_validate(item)
        except ValidationError:
            result.skipped += 1
            continue
        latest = raw.latestWeight or (raw.allWeightMetrics[-1] if raw.allWeightMetrics else None)
        result.records.append(
            WeightRecord(
                calendar_date=raw.summaryDate,
                weight_kg=_kilograms(latest.weight) if latest else None,
                bmi=_within(latest.bmi, 10, 70) if latest else None,
                body_fat_pct=_within(latest.bodyFat, 2, 70) if latest else None,
                entries=_within_int(raw.numOfWeightEntries, 0, 100),
            )
        )
    if result.skipped:
        log.warning("skipped %d weight records that failed validation", result.skipped)
    return result
