"""Real-data check: what sync stored, reported as structure and counts, never health values.

Answers OPEN_QUESTIONS 1-3 on the owner's own database:

1. Did sync, FIT parsing and the wellness datasets work? Runs, synced ranges, file and parse
   outcomes, rows per table and how many rows carry their key measure.
2. Which unit Garmin uses for weight: which branch of `normalize._kilograms` (grams if >= 1000)
   the stored weigh-ins fall into, and whether the result is plausible.
3. How large `activity_sample` is, in rows and bytes.

Follow-ups from the first real run: which keys the raw sleep payloads carry (sleep average HR
was never stored), how far apart consecutive samples are (for phase 2's gap rule), and per-sport
coverage of laps, HR and power.

M6a follow-up (ADR 0013, real-data findings): threshold coverage. For each threshold field in the raw activity
responses, how many activities carry it, how many distinct values it takes (a profile value copied
into every activity stays at one or two), and which unit or scale its values fit; plus what the
normalized columns, the HR zone history and the threshold timeline hold, as counts per code.

Prints one JSON object on stdout: counts, byte sizes, day counts and Garmin's key names only. No
measured value, date, ID or free text, so the output can be shared with a development session.
Read-only; makes no Garmin requests.

Run it in the app container, which has the database URL and the installed package (from the
repository root, with the stack running):

    docker compose run --rm --no-deps --entrypoint python -v "<abs path>/scripts:/scripts:ro" \
        app /scripts/real_data_check.py
"""

import json
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import ColumnElement, func, select, text
from sqlalchemy.orm import Session

from garminagent.analytics.sports import GROUPS, sport_group
from garminagent.config import get_settings
from garminagent.normalize import CM_PER_MS_TO_MPS, THRESHOLD_RANGES
from garminagent.storage.db import make_engine, make_session_factory
from garminagent.storage.models import (
    Activity,
    ActivityFile,
    ActivityLap,
    ActivitySample,
    AthleteThreshold,
    AthleteZones,
    DailySummary,
    HrvSummary,
    RawPayload,
    SleepSummary,
    SyncCursor,
    SyncRun,
    WeightDaily,
)
from garminagent.sync import ACTIVITIES, HR_ZONES, SLEEP, WEIGHT

RECENT_RUNS = 10
# Key paths are reported two levels deep (`sleepScores.overall`), capped in number in case a
# payload uses data as keys.
KEY_DEPTH = 2
MAX_KEYS = 300
# Seconds between consecutive samples of one activity: (label, low, high), inclusive.
GAP_BUCKETS = (
    ("1", 1, 1),
    ("2", 2, 2),
    ("3", 3, 3),
    ("4-5", 4, 5),
    ("6-10", 6, 10),
    ("11-30", 11, 30),
    ("31-120", 31, 120),
    ("over_120", 121, None),
)
# Mirrors normalize._kilograms: values from 1000 up are taken as grams; 20-300 kg is plausible.
GRAMS_FROM = 1000
PLAUSIBLE_KG = (20.0, 300.0)
SAMPLE_COLUMNS = ("heart_rate", "cadence", "power_w", "speed_mps", "distance_m", "altitude_m", "temperature_c")
MEASURE_COLUMNS: dict[str, tuple[Any, list[Any]]] = {
    "daily_summary": (DailySummary, [DailySummary.resting_hr, DailySummary.stress_avg, DailySummary.body_battery_max]),
    "sleep_summary": (SleepSummary, [SleepSummary.sleep_s, SleepSummary.sleep_score, SleepSummary.avg_hr]),
    "hrv_summary": (HrvSummary, [HrvSummary.last_night_avg, HrvSummary.status]),
    "weight_daily": (WeightDaily, [WeightDaily.weight_kg, WeightDaily.body_fat_pct]),
}
# Raw activity fields normalize reads into threshold columns, and the column each one fills.
THRESHOLD_FIELDS = {
    "lactateThresholdBpm": "lactate_threshold_hr",
    "getlactateThresholdSpeed": "threshold_speed_mps",
    "maxFtp": "ftp_w",
    "trainingStressScore": "garmin_tss",
    "intensityFactor": "garmin_if",
}
# Raw activity key names that look threshold-related, so a spelling other than the spec's shows.
THRESHOLD_KEY_HINTS = ("threshold", "ftp", "stress", "intensity")
# normalize keeps intensityFactor in 0-3 (a fraction, 0.80); 3-300 would be a percentage.
IF_FRACTION_MAX = 3.0
IF_PERCENT_MAX = 300.0
ZONE_FIELDS = ("max_hr", "resting_hr", "lthr")
CURSOR_FOR_TABLE = {
    "daily_summary": "daily_summaries",
    "sleep_summary": "sleep",
    "hrv_summary": "hrv",
    "weight_daily": WEIGHT,
}


def _count(session: Session, model: Any, *where: Any) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _bytes(session: Session, relation: str) -> int:
    return session.scalar(text("SELECT pg_total_relation_size(CAST(:r AS regclass))"), {"r": relation}) or 0


def sync_section(session: Session, today: date) -> dict[str, Any]:
    now = datetime.now(UTC)
    recent = session.scalars(select(SyncRun).order_by(SyncRun.finished_at.desc()).limit(RECENT_RUNS)).all()
    stopped = session.execute(select(SyncRun.stopped, func.count()).group_by(SyncRun.stopped)).all()
    cursors = session.scalars(select(SyncCursor)).all()
    return {
        "runs_total": sum(n for _, n in stopped),
        "runs_by_stop_reason": {str(reason or "complete"): n for reason, n in stopped},
        "recent_runs": [
            {
                "stopped": run.stopped or "complete",
                "requests": run.requests,
                "hours_ago": round((now - run.finished_at).total_seconds() / 3600, 1),
            }
            for run in recent
        ],
        "datasets": {
            c.dataset: {
                "days_synced": (c.synced_through - c.synced_from).days + 1 if c.synced_from and c.synced_through else 0,
                "through_last_2_days": bool(c.synced_through and (today - c.synced_through).days <= 2),
            }
            for c in sorted(cursors, key=lambda c: c.dataset)
        },
    }


def files_section(session: Session) -> dict[str, Any]:
    file_status = session.execute(select(Activity.file_status, func.count()).group_by(Activity.file_status)).all()
    fits = session.execute(
        select(ActivityFile.kind, ActivityFile.parse_status, ActivityFile.parser_version, func.count()).group_by(
            ActivityFile.kind, ActivityFile.parse_status, ActivityFile.parser_version
        )
    ).all()
    with_samples = select(ActivitySample.activity_id).distinct()
    parsed = select(ActivityFile.activity_id).where(ActivityFile.parse_status == "parsed").distinct()
    return {
        "activities": _count(session, Activity),
        "activity_file_status": {str(status or "untried"): n for status, n in file_status},
        "files": [
            {"kind": kind, "parse_status": status or "unparsed", "parser_version": version, "count": n}
            for kind, status, version, n in fits
        ],
        "activities_with_samples": session.scalar(select(func.count()).select_from(with_samples.subquery())) or 0,
        "parsed_without_samples": session.scalar(
            select(func.count()).select_from(parsed.except_(with_samples).subquery())
        )
        or 0,
        "activities_with_laps": session.scalar(
            select(func.count()).select_from(select(ActivityLap.activity_id).distinct().subquery())
        )
        or 0,
    }


def wellness_section(session: Session) -> dict[str, Any]:
    days = {c.dataset: c for c in session.scalars(select(SyncCursor)).all()}
    report: dict[str, Any] = {}
    for table, (model, columns) in MEASURE_COLUMNS.items():
        cursor = days.get(CURSOR_FOR_TABLE[table])
        synced = (
            (cursor.synced_through - cursor.synced_from).days + 1
            if cursor and cursor.synced_from and cursor.synced_through
            else 0
        )
        report[table] = {
            "rows": _count(session, model),
            "days_synced": synced,
            "rows_with": {col.key: _count(session, model, col.is_not(None)) for col in columns},
        }
    return report


def _objects(value: object) -> list[dict[str, object]]:
    """The JSON objects in `value` (one object, or a list of them); anything else is skipped."""
    items = cast(list[object], value) if isinstance(value, list) else [value]
    return [cast(dict[str, object], item) for item in items if isinstance(item, dict)]


def _classify(value: object, counts: Counter[str]) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        counts["missing" if value is None else "not_a_number"] += 1
        return
    branch = "grams_branch" if value >= GRAMS_FROM else "kg_branch"
    kg = value / 1000 if value >= GRAMS_FROM else value
    plausible = PLAUSIBLE_KG[0] <= kg <= PLAUSIBLE_KG[1]
    counts[f"{branch}_{'plausible' if plausible else 'implausible'}"] += 1


def weight_section(session: Session) -> dict[str, Any]:
    rows = session.execute(
        select(RawPayload.request_key, RawPayload.payload)
        .where(RawPayload.endpoint == WEIGHT)
        .order_by(RawPayload.request_key, RawPayload.last_fetched_at.desc())
    ).all()
    latest_per_request: dict[str, object] = {}
    for key, payload in rows:
        latest_per_request.setdefault(key, payload)  # first row per key is the newest revision

    latest_counts: Counter[str] = Counter()
    all_counts: Counter[str] = Counter()
    day_keys: set[str] = set()
    weight_keys: set[str] = set()
    days = 0
    for payload in _objects(list(latest_per_request.values())):
        for item in _objects(payload.get("dailyWeightSummaries")):
            days += 1
            day_keys.update(item)
            latest, all_metrics = _objects(item.get("latestWeight")), _objects(item.get("allWeightMetrics"))
            for weigh_in in latest + all_metrics:
                weight_keys.update(weigh_in)
            for weigh_in in latest:
                _classify(weigh_in.get("weight"), latest_counts)
            for weigh_in in all_metrics:
                _classify(weigh_in.get("weight"), all_counts)

    plausible = {
        branch: all_counts[f"{branch}_branch_plausible"] + latest_counts[f"{branch}_branch_plausible"]
        for branch in ("grams", "kg")
    }
    if not plausible["grams"] and not plausible["kg"]:
        unit = "no data"
    elif plausible["grams"] and plausible["kg"]:
        unit = "mixed"
    else:
        unit = "grams" if plausible["grams"] else "kilograms"
    return {
        "raw_requests": len(latest_per_request),
        "day_items": days,
        "latest_weight": dict(sorted(latest_counts.items())),
        "all_weight_metrics": dict(sorted(all_counts.items())),
        "unit": unit,
        "day_item_keys": sorted(day_keys),
        "weight_keys": sorted(weight_keys),
    }


def _key_paths(obj: dict[str, object], prefix: str, depth: int, counts: Counter[str]) -> None:
    for key, value in obj.items():
        path = f"{prefix}{key}"
        counts[path] += value is not None  # a key that is present but always null shows as 0
        if depth > 1 and isinstance(value, dict):  # nested objects only; lists aren't walked
            _key_paths(cast(dict[str, object], value), f"{path}.", depth - 1, counts)


def sleep_keys_section(session: Session) -> dict[str, Any]:
    """Non-null counts per key path in the newest revision of each raw sleep response."""
    rows = session.execute(
        select(RawPayload.request_key, RawPayload.payload)
        .where(RawPayload.endpoint == SLEEP)
        .order_by(RawPayload.request_key, RawPayload.last_fetched_at.desc())
    ).all()
    latest_per_request: dict[str, object] = {}
    for key, payload in rows:
        latest_per_request.setdefault(key, payload)
    counts: Counter[str] = Counter()
    nights = 0
    for payload in latest_per_request.values():
        for night in _objects(payload):
            nights += 1
            _key_paths(night, "", KEY_DEPTH, counts)
    paths = sorted(counts)
    return {
        "nights": nights,
        "key_paths_total": len(paths),
        "non_null_by_key": {path: counts[path] for path in paths[:MAX_KEYS]},
    }


def gaps_section(session: Session) -> dict[str, Any]:
    """How far apart consecutive samples are, as counts and total seconds per bucket."""
    gap = (
        ActivitySample.elapsed_s
        - func.lag(ActivitySample.elapsed_s).over(
            partition_by=(ActivitySample.athlete_id, ActivitySample.activity_id), order_by=ActivitySample.elapsed_s
        )
    ).label("gap")
    gaps = select(gap).subquery()
    rows = session.execute(select(gaps.c.gap, func.count()).where(gaps.c.gap.is_not(None)).group_by(gaps.c.gap)).all()
    buckets = {label: {"count": 0, "seconds": 0} for label, _, _ in GAP_BUCKETS}
    for seconds, n in rows:
        for label, low, high in GAP_BUCKETS:
            if seconds >= low and (high is None or seconds <= high):
                buckets[label]["count"] += n
                buckets[label]["seconds"] += seconds * n
                break
    return buckets


def by_sport_section(session: Session) -> dict[str, Any]:
    """Per Garmin activity type: activities, and how many have laps, HR samples and power samples."""

    def activities_with(model: Any, *where: Any) -> Any:
        return select(model.activity_id).where(*where).distinct()

    laps = activities_with(ActivityLap)
    hr = activities_with(ActivitySample, ActivitySample.heart_rate.is_not(None))
    power = activities_with(ActivitySample, ActivitySample.power_w.is_not(None))
    rows = session.execute(
        select(
            Activity.sport,
            func.count(),
            func.count().filter(Activity.activity_id.in_(laps)),
            func.count().filter(Activity.activity_id.in_(hr)),
            func.count().filter(Activity.activity_id.in_(power)),
        ).group_by(Activity.sport)
    ).all()
    return {
        str(sport or "unknown"): {"activities": n, "with_laps": n_laps, "with_hr": n_hr, "with_power": n_power}
        for sport, n, n_laps, n_hr, n_power in sorted(rows, key=lambda r: (-r[1], str(r[0])))
    }


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _latest_activities(session: Session) -> list[dict[str, object]]:
    """The newest raw version of each activity, as renormalize picks it."""
    rows = session.execute(
        select(RawPayload.payload)
        .where(RawPayload.endpoint == ACTIVITIES)
        .order_by(RawPayload.last_fetched_at.desc(), RawPayload.id.desc())
    ).all()
    seen: set[object] = set()
    latest: list[dict[str, object]] = []
    for (payload,) in rows:
        for item in reversed(_objects(payload)):  # within one response the last item wins
            key = item.get("activityId")
            if key is None or key in seen:
                continue
            seen.add(key)
            latest.append(item)
    return latest


def _speed_unit(value: float, counts: Counter[str]) -> None:
    low, high = THRESHOLD_RANGES["threshold_speed"]
    as_cm_per_ms = low <= value * CM_PER_MS_TO_MPS <= high  # what normalize assumes
    as_m_per_s = low <= value <= high
    labels = {(True, True): "both", (True, False): "cm_per_ms_only", (False, True): "m_per_s_only"}
    counts["plausible_as_" + labels.get((as_cm_per_ms, as_m_per_s), "neither")] += 1


def _if_scale(value: float, counts: Counter[str]) -> None:
    if 0 <= value <= IF_FRACTION_MAX:
        counts["fraction_0_to_3"] += 1
    elif IF_FRACTION_MAX < value <= IF_PERCENT_MAX:
        counts["percent_3_to_300"] += 1
    else:
        counts["other"] += 1


def _type_key(item: dict[str, object]) -> str | None:
    ref = item.get("activityType")
    key = cast(dict[str, object], ref).get("typeKey") if isinstance(ref, dict) else None
    return key if isinstance(key, str) else None


def _raw_threshold_fields(latest: list[dict[str, object]]) -> dict[str, Any]:
    fields: dict[str, Counter[str]] = {name: Counter() for name in THRESHOLD_FIELDS}
    not_a_number: Counter[str] = Counter()
    distinct: dict[str, dict[str, set[float]]] = {name: {} for name in THRESHOLD_FIELDS}
    speed_units: Counter[str] = Counter()
    if_scale: Counter[str] = Counter()
    hint_keys: Counter[str] = Counter()
    for item in latest:
        group = sport_group(_type_key(item))
        for key, value in item.items():
            if value is not None and any(hint in key.lower() for hint in THRESHOLD_KEY_HINTS):
                hint_keys[key] += 1
        for name in THRESHOLD_FIELDS:
            value = item.get(name)
            if value is None:
                continue
            number = _number(value)
            if number is None:
                not_a_number[name] += 1
                continue
            fields[name][group] += 1
            distinct[name].setdefault(group, set()).add(number)
            if name == "getlactateThresholdSpeed":
                _speed_unit(number, speed_units)
            elif name == "intensityFactor":
                _if_scale(number, if_scale)
    return {
        "fields": {
            name: {
                "present": sum(fields[name].values()),
                "not_a_number": not_a_number[name],
                "per_group": {g: fields[name][g] for g in GROUPS if fields[name][g]},
                # One or two distinct values across many activities suggests a profile value.
                "distinct_values_per_group": {g: len(distinct[name][g]) for g in GROUPS if g in distinct[name]},
            }
            for name in THRESHOLD_FIELDS
        },
        "threshold_speed_unit": dict(sorted(speed_units.items())),
        "intensity_factor_scale": dict(sorted(if_scale.items())),
        "threshold_like_keys": dict(sorted(hint_keys.items())[:MAX_KEYS]),
    }


def _stored_threshold_columns(session: Session) -> dict[str, dict[str, int]]:
    columns = list(THRESHOLD_FIELDS.values())
    rows = session.execute(
        select(Activity.sport, *(func.count(getattr(Activity, column)) for column in columns)).group_by(Activity.sport)
    ).all()
    stored: dict[str, Counter[str]] = {column: Counter() for column in columns}
    for sport, *counts in rows:
        for column, n in zip(columns, counts, strict=True):
            stored[column][sport_group(sport)] += n
    return {column: {g: stored[column][g] for g in GROUPS if stored[column][g]} for column in columns}


def _zones(session: Session) -> dict[str, Any]:
    history: dict[str, dict[str, Any]] = {}
    for zone in session.scalars(select(AthleteZones)).all():
        entry = history.setdefault(
            zone.sport, {"rows": 0, "methods": Counter[str](), "with_all_floors": 0, **dict.fromkeys(ZONE_FIELDS, 0)}
        )
        entry["rows"] += 1
        entry["methods"][zone.training_method or "unknown"] += 1
        floors = (zone.zone1_floor, zone.zone2_floor, zone.zone3_floor, zone.zone4_floor, zone.zone5_floor)
        entry["with_all_floors"] += all(f is not None for f in floors)
        for f in ZONE_FIELDS:
            entry[f] += getattr(zone, f) is not None
    newest = session.scalar(
        select(RawPayload.payload)
        .where(RawPayload.endpoint == HR_ZONES)
        .order_by(RawPayload.last_fetched_at.desc(), RawPayload.id.desc())
        .limit(1)
    )
    items = _objects(cast(object, newest)) if isinstance(newest, list) else []
    keys: Counter[str] = Counter()
    for item in items:
        _key_paths(item, "", 1, keys)
    return {
        "raw_responses": _count(session, RawPayload, RawPayload.endpoint == HR_ZONES),
        "newest_response_items": len(items),
        "newest_response_keys": dict(sorted(keys.items())[:MAX_KEYS]),
        "history_per_sport": {
            sport: {
                "rows": e["rows"],
                "methods": dict(sorted(e["methods"].items())),
                "with_all_floors": e["with_all_floors"],
                **{f"with_{f}": e[f] for f in ZONE_FIELDS},
            }
            for sport, e in sorted(history.items())
        },
    }


def _timeline(session: Session) -> dict[str, dict[str, dict[str, int]]]:
    rows = session.execute(
        select(AthleteThreshold.source, AthleteThreshold.value_type, AthleteThreshold.sport, func.count())
        .group_by(AthleteThreshold.source, AthleteThreshold.value_type, AthleteThreshold.sport)
        .order_by(AthleteThreshold.source, AthleteThreshold.value_type, AthleteThreshold.sport)
    ).all()
    timeline: dict[str, dict[str, dict[str, int]]] = {}
    for source, value_type, sport, n in rows:
        timeline.setdefault(source, {}).setdefault(value_type, {})[sport] = n
    return timeline


def thresholds_section(session: Session) -> dict[str, Any]:
    """Threshold coverage (ADR 0013, real-data findings): raw activity fields, the normalized columns they
    fill, the HR zone history and the threshold timeline rows per source, type and sport."""
    latest = _latest_activities(session)
    return {
        "raw_activities": len(latest),
        **_raw_threshold_fields(latest),
        "stored_columns_per_group": _stored_threshold_columns(session),
        "zones": _zones(session),
        "timeline_rows": _timeline(session),
    }


def samples_section(session: Session) -> dict[str, Any]:
    per_activity = (
        select(ActivitySample.activity_id, func.count().label("n")).group_by(ActivitySample.activity_id).subquery()
    )
    n = cast(ColumnElement[int], per_activity.c.n)
    stats = session.execute(select(func.count(), func.avg(n), func.max(n)).select_from(per_activity)).one()
    rows = _count(session, ActivitySample)
    blob_count, blob_bytes = session.execute(select(func.count(), func.sum(ActivityFile.size_bytes))).one()
    return {
        "rows": rows,
        "activities": stats[0] or 0,
        "rows_per_activity_avg": round(float(stats[1] or 0)),
        "rows_per_activity_max": stats[2] or 0,
        "rows_with": {
            col: _count(session, ActivitySample, getattr(ActivitySample, col).is_not(None)) for col in SAMPLE_COLUMNS
        },
        "bytes": {
            "activity_sample": _bytes(session, "activity_sample"),
            "activity_lap": _bytes(session, "activity_lap"),
            "raw_payload": _bytes(session, "raw_payload"),
            "database": session.scalar(text("SELECT pg_database_size(current_database())")) or 0,
            "activity_files_in_blob_store": int(blob_bytes or 0),
        },
        "activity_files": blob_count or 0,
    }


def collect(session: Session, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    return {
        "sync": sync_section(session, today),
        "files": files_section(session),
        "tables": {
            "activity": _count(session, Activity),
            "activity_sample": _count(session, ActivitySample),
            "activity_lap": _count(session, ActivityLap),
            "raw_payload": _count(session, RawPayload),
        },
        "wellness": wellness_section(session),
        "weight_units": weight_section(session),
        "samples": samples_section(session),
        "sleep_keys": sleep_keys_section(session),
        "sample_gaps_s": gaps_section(session),
        "by_sport": by_sport_section(session),
        "thresholds": thresholds_section(session),
    }


def main() -> None:
    engine = make_engine(get_settings().database_url.get_secret_value())
    try:
        with make_session_factory(engine)() as session:  # read-only
            report = collect(session)
    finally:
        engine.dispose()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
