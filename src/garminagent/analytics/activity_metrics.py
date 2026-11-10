"""Keeps `activity_metrics` current: one row per activity (ADR 0013, M6b).

**Recompute by input fingerprint.** Each row stores a hash of everything its values depend on:
the activity's day and sport group, its FIT files (content hash, parse status and parser
version, which decide what `activity_sample` holds), the thresholds and zone floors resolved
for that day, the HR load coefficient and ALGO_VERSION. A row is recomputed only when it is
missing or its fingerprint differs. `activity.updated_at` is never consulted: every sync
rewrites it for the last 7 days of activities whether or not anything changed.

**Capped per run.** At most `max_activities` rows are computed per run, newest activities
first, so the first catch-up spreads over runs like the backfill; the rest are counted as
pending. `analyze --full` passes no cap.

The coefficient is part of the fingerprint only. A reader who has every other input could
tell which of the two values was used, but such a reader also has the HR load and the HR
samples, which tell the same; agents see neither (ADR 0013, M6f).
"""

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from garminagent.analytics.metrics import ALGO_VERSION, Metrics, Point, Thresholds, compute
from garminagent.analytics.sports import SportGroup, sport_group
from garminagent.analytics.thresholds import Resolved, ThresholdTimeline, ValueType, load_timeline
from garminagent.analytics.zones import ZoneFloors, ZoneHistory, load_zone_history
from garminagent.storage.models import Activity, ActivityFile, ActivityMetrics, ActivitySample

log = logging.getLogger(__name__)

# Activities per transaction: bounds memory (samples are loaded per batch) and how much one
# failure rolls back.
BATCH: Final = 25
USED_THRESHOLDS: Final[tuple[ValueType, ...]] = ("ftp", "lthr", "max_hr", "resting_hr")


@dataclass(frozen=True)
class ActivityInput:
    activity_id: int
    day: date
    group: SportGroup
    fit_files: tuple[tuple[str, str | None, int | None], ...]  # (sha256, parse_status, parser_version)


@dataclass(frozen=True)
class Resolution:
    thresholds: dict[ValueType, Resolved | None]
    zones: ZoneFloors | None

    @property
    def backfilled(self) -> bool:
        return any(r is not None and r.backfilled for r in self.thresholds.values()) or bool(
            self.zones and self.zones.backfilled
        )

    def for_metrics(self) -> Thresholds:
        def value(kind: ValueType) -> float | None:
            resolved = self.thresholds[kind]
            return resolved.value if resolved else None

        return Thresholds(
            ftp_w=value("ftp"),
            lthr=value("lthr"),
            max_hr=value("max_hr"),
            resting_hr=value("resting_hr"),
            zone_floors=self.zones.floors if self.zones else None,
        )


def resolve(timeline: ThresholdTimeline, zones: ZoneHistory, activity: ActivityInput) -> Resolution:
    at = timeline.at(activity.day, activity.group)
    return Resolution({kind: at[kind] for kind in USED_THRESHOLDS}, zones.at(activity.day, activity.group))


def fingerprint(activity: ActivityInput, resolution: Resolution, trimp_b: float | None) -> str:
    def threshold(r: Resolved | None) -> list[Any] | None:
        return None if r is None else [r.value, r.source, r.sport, r.valid_from.isoformat(), r.backfilled]

    z = resolution.zones
    inputs = {
        "algo": ALGO_VERSION,
        "day": activity.day.isoformat(),
        "group": activity.group,
        "files": [list(f) for f in sorted(activity.fit_files, key=lambda f: f[0])],
        "thresholds": {kind: threshold(r) for kind, r in resolution.thresholds.items()},
        "zones": None if z is None else [z.zone_sport, z.valid_from.isoformat(), list(z.floors), z.backfilled],
        "trimp_b": trimp_b,
    }
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def row_values(activity: ActivityInput, resolution: Resolution, metrics: Metrics, digest: str) -> dict[str, Any]:
    def threshold(kind: ValueType) -> float | None:
        r = resolution.thresholds[kind]
        return r.value if r else None

    zones, levels, split = metrics.hr_zone_s, metrics.power_level_s, metrics.three_zone_s
    resting = resolution.thresholds["resting_hr"]
    return {
        "activity_id": activity.activity_id,
        "activity_date": activity.day,
        "sport_group": activity.group,
        "algo_version": ALGO_VERSION,
        "input_fingerprint": digest,
        "moving_s": metrics.moving_s,
        "hr_coverage": metrics.hr_coverage,
        "power_coverage": metrics.power_coverage,
        "speed_coverage": metrics.speed_coverage,
        "weighted_power_w": metrics.weighted_power_w,
        "intensity": metrics.intensity,
        "power_load": metrics.power_load,
        "hr_load": metrics.hr_load,
        "load": metrics.load,
        "load_method": metrics.load_method,
        **{f"hr_zone_{k}_s": zones[k] if zones else None for k in range(6)},
        **{f"power_level_{k + 1}_s": levels[k] if levels else None for k in range(7)},
        **{f"three_zone_{name}_s": split[k] if split else None for k, name in enumerate(("low", "mid", "high"))},
        "efficiency_factor": metrics.efficiency_factor,
        "ef_basis": metrics.ef_basis,
        "decoupling": metrics.decoupling,
        "ftp_w": threshold("ftp"),
        "lthr_bpm": threshold("lthr"),
        "max_hr_bpm": threshold("max_hr"),
        "resting_hr_bpm": threshold("resting_hr"),
        "resting_hr_source": resting.source if resting else None,
        "hr_zones_sport": resolution.zones.zone_sport if resolution.zones else None,
        "hr_zones_valid_from": resolution.zones.valid_from if resolution.zones else None,
        "thresholds_backfilled": resolution.backfilled,
    }


@dataclass
class MetricsCounts:
    activities: int = 0
    current: int = 0
    computed: int = 0
    pending: int = 0
    no_samples: int = 0
    backfilled: int = 0
    hr_load_not_configured: int = 0
    errors: int = 0
    load_methods: dict[str, int] | None = None  # rows per load method after the run, all rows


def update_metrics(
    sessions: sessionmaker[Session], athlete_id: int, trimp_b: float | None, max_activities: int | None
) -> MetricsCounts:
    counts = MetricsCounts()
    with sessions() as session:
        activities = _activity_inputs(session, athlete_id)
        timeline = load_timeline(session, athlete_id)
        zones = load_zone_history(session, athlete_id)
        stored = {
            r.activity_id: r.input_fingerprint
            for r in session.execute(
                select(ActivityMetrics.activity_id, ActivityMetrics.input_fingerprint).where(
                    ActivityMetrics.athlete_id == athlete_id
                )
            )
        }
    stale: list[tuple[ActivityInput, Resolution, str]] = []
    for activity in activities:  # newest first
        resolution = resolve(timeline, zones, activity)
        digest = fingerprint(activity, resolution, trimp_b)
        if stored.get(activity.activity_id) != digest:
            stale.append((activity, resolution, digest))
    counts.activities = len(activities)
    counts.current = len(activities) - len(stale)
    todo = stale if max_activities is None else stale[:max_activities]
    counts.pending = len(stale) - len(todo)

    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        with sessions.begin() as session:
            points = _points(session, athlete_id, [a.activity_id for a, _, _ in batch])
            rows: list[dict[str, Any]] = []
            for activity, resolution, digest in batch:
                try:
                    metrics = compute(
                        points.get(activity.activity_id, []), activity.group, resolution.for_metrics(), trimp_b
                    )
                except Exception as exc:
                    # A bug in one activity's arithmetic must not stop the others. Class name
                    # only: a message could quote values.
                    log.error("analyze: metrics for one activity failed: %s", exc.__class__.__name__)
                    counts.errors += 1
                    continue
                rows.append({"athlete_id": athlete_id, **row_values(activity, resolution, metrics, digest)})
                counts.computed += 1
                counts.no_samples += metrics.moving_s is None
                counts.backfilled += resolution.backfilled
                counts.hr_load_not_configured += metrics.hr_load_not_configured
            _upsert(session, rows)

    with sessions() as session:
        counts.load_methods = {
            method: n
            for method, n in session.execute(
                select(ActivityMetrics.load_method, func.count())
                .where(ActivityMetrics.athlete_id == athlete_id)
                .group_by(ActivityMetrics.load_method)
            )
        }
    return counts


def _activity_inputs(session: Session, athlete_id: int) -> list[ActivityInput]:
    files: dict[int, list[tuple[str, str | None, int | None]]] = defaultdict(list)
    for r in session.execute(
        select(
            ActivityFile.activity_id, ActivityFile.sha256, ActivityFile.parse_status, ActivityFile.parser_version
        ).where(ActivityFile.athlete_id == athlete_id, ActivityFile.kind == "fit")
    ):
        files[r.activity_id].append((r.sha256, r.parse_status, r.parser_version))
    rows = session.execute(
        select(Activity.activity_id, Activity.start_time_utc, Activity.start_time_local, Activity.sport)
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_time_utc.desc(), Activity.activity_id.desc())
    ).all()
    return [
        ActivityInput(
            activity_id=r.activity_id,
            # The local day when known, else UTC; a session crossing midnight counts on its start day.
            day=(r.start_time_local or r.start_time_utc).date(),
            group=sport_group(r.sport),
            fit_files=tuple(files.get(r.activity_id, ())),
        )
        for r in rows
    ]


def _points(session: Session, athlete_id: int, activity_ids: Sequence[int]) -> dict[int, list[Point]]:
    points: dict[int, list[Point]] = defaultdict(list)
    for r in session.execute(
        select(
            ActivitySample.activity_id,
            ActivitySample.elapsed_s,
            ActivitySample.heart_rate,
            ActivitySample.power_w,
            ActivitySample.speed_mps,
        ).where(ActivitySample.athlete_id == athlete_id, ActivitySample.activity_id.in_(activity_ids))
    ):
        points[r.activity_id].append(Point(r.elapsed_s, r.heart_rate, r.power_w, r.speed_mps))
    return points


def _upsert(session: Session, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = insert(ActivityMetrics).values(rows)
    keys = ("athlete_id", "activity_id")
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=list(keys),
            set_={
                **{c: stmt.excluded[c] for c in rows[0] if c not in keys},
                "updated_at": func.now(),
            },
        )
    )
