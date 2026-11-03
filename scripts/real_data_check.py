"""Real-data check: what sync stored, reported as structure and counts, never health values.

Answers OPEN_QUESTIONS 1-3 on the owner's own database:

1. Did sync, FIT parsing and the wellness datasets work? Runs, synced ranges, file and parse
   outcomes, rows per table and how many rows carry their key measure.
2. Which unit Garmin uses for weight: which branch of `normalize._kilograms` (grams if >= 1000)
   the stored weigh-ins fall into, and whether the result is plausible.
3. How large `activity_sample` is, in rows and bytes.

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

from garminagent.config import get_settings
from garminagent.storage.db import make_engine, make_session_factory
from garminagent.storage.models import (
    Activity,
    ActivityFile,
    ActivityLap,
    ActivitySample,
    DailySummary,
    HrvSummary,
    RawPayload,
    SleepSummary,
    SyncCursor,
    SyncRun,
    WeightDaily,
)
from garminagent.sync import WEIGHT

RECENT_RUNS = 10
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
