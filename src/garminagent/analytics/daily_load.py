"""Keeps `daily_load` current: fitness, fatigue, form and ramp per day and sport group (ADR 0013,
M6c), from the chosen load in `activity_metrics`.

**Full recompute, minimal writes.** The series is rebuilt from scratch every run (a few thousand
rows), then compared with what is stored: only rows that differ are written and rows outside the
new series are deleted, so `updated_at` means "last changed", and an unchanged history costs no
writes. No fingerprint is needed: every input is read again each time.

**Every day, every group that has trained.** The series runs from the first activity's day to the
latest day with data (the later of the last activity and the last daily summary), so form keeps
recovering through rest days without a timezone setting deciding what "today" is. Group `all`
holds the total; each sport group with at least one activity gets its own rows over the same
range, with its own fitness and fatigue (every group counts toward `all`, ADR 0013).

**Missing load stays visible.** An activity whose metrics row has no load (`load_method` none),
or that has no metrics row yet (pending under the per-run cap), adds 0 and is counted on its day
(`activities_unloaded`, `activities_pending`), so a reader can tell a rest day from a gap. An
activity whose metrics row is stale but not yet recomputed keeps its stored load until then.

**Definitions** (change one and bump ALGO_VERSION):

- fitness_t = fitness_{t-1} + (load_t - fitness_{t-1}) / 42, fatigue the same with 7, both
  starting from 0 the day before the series.
- form_t = fitness_{t-1} - fatigue_{t-1}: freshness going into the day, before its training.
- ramp_t = fitness_t - fitness_{t-7}: the 7-day change in fitness (owner's decision,
  2026-11-12), NULL for the first 7 days. No limit is applied here; limits are phase 3's
  (OPEN_QUESTIONS 11).
- fitness_reliable once the series has 126 days (3 x 42) of history: before that, fitness,
  form and ramp are dominated by the zero start.
"""

import logging
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import astuple, dataclass, field, fields
from datetime import date, timedelta
from typing import Any, Final, Literal

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from garminagent.analytics.sports import GROUPS, SportGroup, sport_group
from garminagent.storage.models import Activity, ActivityMetrics, DailyLoad, DailySummary

log = logging.getLogger(__name__)

ALGO_VERSION: Final = 1
FITNESS_DAYS: Final = 42
FATIGUE_DAYS: Final = 7
RAMP_DAYS: Final = 7
RELIABLE_DAYS: Final = 3 * FITNESS_DAYS
# Rows per INSERT: 16 columns each stays well under Postgres's 65535 bind parameters.
WRITE_BATCH: Final = 1000

type LoadGroup = Literal["all"] | SportGroup
LOAD_GROUPS: Final[tuple[LoadGroup, ...]] = ("all", *GROUPS)


@dataclass(frozen=True)
class ActivityLoad:
    """One activity as the series sees it. `load` is None when the activity has no load;
    `pending` when it has no metrics row yet."""

    day: date
    group: SportGroup
    load: float | None
    moving_s: int | None
    backfilled: bool
    pending: bool


@dataclass(frozen=True)
class DayLoad:
    """One `daily_load` row without its keys; field order is the comparison order."""

    load: float
    sessions: int
    moving_s: int
    activities_unloaded: int
    activities_pending: int
    thresholds_backfilled: bool
    fitness: float
    fatigue: float
    form: float
    ramp: float | None
    history_days: int
    fitness_reliable: bool
    algo_version: int = ALGO_VERSION


type SeriesKey = tuple[date, str]  # (day, `all` or a sport group)


@dataclass
class _DayTotals:
    loads: list[float] = field(default_factory=list[float])
    sessions: int = 0
    moving_s: int = 0
    unloaded: int = 0
    pending: int = 0
    backfilled: bool = False

    @property
    def load(self) -> float:
        # fsum is exactly rounded, so the total doesn't depend on the order Postgres returns
        # activities in; a plain running sum of three or more loads would, and would make
        # unchanged days compare unequal.
        return math.fsum(self.loads)

    def add(self, activity: ActivityLoad) -> None:
        if activity.load is not None:
            self.loads.append(activity.load)
        self.sessions += 1
        self.moving_s += activity.moving_s or 0
        self.unloaded += activity.load is None and not activity.pending
        self.pending += activity.pending
        # Only a load that counts can be approximate; an unloaded activity adds nothing to flag.
        self.backfilled |= activity.backfilled and activity.load is not None


def build_series(activities: Iterable[ActivityLoad], last_data_day: date | None = None) -> dict[SeriesKey, DayLoad]:
    """The whole series: a row per day from the first activity's day to the later of the last
    activity's day and `last_data_day`, for `all` and each group with an activity. Pure."""
    totals: dict[SeriesKey, _DayTotals] = defaultdict(_DayTotals)
    groups: set[LoadGroup] = set()
    for activity in activities:
        totals[activity.day, "all"].add(activity)
        totals[activity.day, activity.group].add(activity)
        groups.update(("all", activity.group))
    if not totals:
        return {}
    days = [day for day, _ in totals]
    first = min(days)
    last = max(max(days), last_data_day or first)
    series: dict[SeriesKey, DayLoad] = {}
    for group in LOAD_GROUPS:
        if group not in groups:
            continue
        fitness = fatigue = 0.0
        history: list[float] = []  # fitness per day so far, for the ramp
        for n in range((last - first).days + 1):
            day = first + timedelta(days=n)
            t = totals.get((day, group), _DayTotals())
            load = t.load
            form = fitness - fatigue
            fitness += (load - fitness) / FITNESS_DAYS
            fatigue += (load - fatigue) / FATIGUE_DAYS
            history.append(fitness)
            series[day, group] = DayLoad(
                load=load,
                sessions=t.sessions,
                moving_s=t.moving_s,
                activities_unloaded=t.unloaded,
                activities_pending=t.pending,
                thresholds_backfilled=t.backfilled,
                fitness=fitness,
                fatigue=fatigue,
                form=form,
                ramp=fitness - history[n - RAMP_DAYS] if n >= RAMP_DAYS else None,
                history_days=n + 1,
                fitness_reliable=n + 1 >= RELIABLE_DAYS,
            )
    return series


@dataclass
class DailyLoadCounts:
    days: int = 0
    rows: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    reliable_days: int = 0
    activities_unloaded: int = 0
    activities_pending: int = 0


def update_daily_load(sessions: sessionmaker[Session], athlete_id: int) -> DailyLoadCounts:
    """Rebuild the series and write the differences, in one transaction."""
    counts = DailyLoadCounts()
    with sessions.begin() as session:
        series = build_series(_activity_loads(session, athlete_id), _last_summary_day(session, athlete_id))
        stored = _stored(session, athlete_id)
        changed = [key for key, row in series.items() if not _same(stored.get(key), row)]
        gone = [key for key in stored if key not in series]
        _upsert(session, athlete_id, [(key, series[key]) for key in changed])
        _delete(session, athlete_id, gone)
    counts.rows = len(series)
    counts.inserted = sum(key not in stored for key in changed)
    counts.updated = len(changed) - counts.inserted
    counts.deleted = len(gone)
    totals = [row for (_, group), row in series.items() if group == "all"]
    counts.days = len(totals)
    counts.reliable_days = sum(row.fitness_reliable for row in totals)
    counts.activities_unloaded = sum(row.activities_unloaded for row in totals)
    counts.activities_pending = sum(row.activities_pending for row in totals)
    return counts


def _activity_loads(session: Session, athlete_id: int) -> list[ActivityLoad]:
    rows = session.execute(
        select(
            Activity.start_time_utc,
            Activity.start_time_local,
            Activity.sport,
            ActivityMetrics.activity_id.is_(None).label("pending"),
            ActivityMetrics.load,
            ActivityMetrics.moving_s,
            ActivityMetrics.thresholds_backfilled,
        )
        .outerjoin(
            ActivityMetrics,
            (ActivityMetrics.athlete_id == Activity.athlete_id) & (ActivityMetrics.activity_id == Activity.activity_id),
        )
        .where(Activity.athlete_id == athlete_id)
    ).all()
    return [
        ActivityLoad(
            # The activity's own day and group, as in M6b: local start day, else UTC.
            day=(r.start_time_local or r.start_time_utc).date(),
            group=sport_group(r.sport),
            load=r.load,
            moving_s=r.moving_s,
            backfilled=bool(r.thresholds_backfilled),
            pending=bool(r.pending),
        )
        for r in rows
    ]


def _last_summary_day(session: Session, athlete_id: int) -> date | None:
    return session.scalar(select(func.max(DailySummary.calendar_date)).where(DailySummary.athlete_id == athlete_id))


VALUE_COLUMNS: Final = tuple(f.name for f in fields(DayLoad))
# Floats round-trip float8 exactly with Postgres's default `extra_float_digits`; the tolerance
# keeps a server setting that rounds them from rewriting every row on every run.
REL_TOL: Final = 1e-12


def _same(stored: DayLoad | None, row: DayLoad) -> bool:
    if stored is None:
        return False
    for a, b in zip(astuple(stored), astuple(row), strict=True):
        if isinstance(a, float) and isinstance(b, float):
            if not math.isclose(a, b, rel_tol=REL_TOL, abs_tol=REL_TOL):
                return False
        elif a != b:
            return False
    return True


def _stored(session: Session, athlete_id: int) -> dict[SeriesKey, DayLoad]:
    columns = [getattr(DailyLoad, name) for name in VALUE_COLUMNS]
    return {
        (r[0], r[1]): DayLoad(*r[2:])
        for r in session.execute(
            select(DailyLoad.calendar_date, DailyLoad.sport_group, *columns).where(DailyLoad.athlete_id == athlete_id)
        )
    }


def _upsert(session: Session, athlete_id: int, rows: Sequence[tuple[SeriesKey, DayLoad]]) -> None:
    for start in range(0, len(rows), WRITE_BATCH):
        values: list[dict[str, Any]] = [
            {
                "athlete_id": athlete_id,
                "calendar_date": day,
                "sport_group": group,
                **dict(zip(VALUE_COLUMNS, astuple(row), strict=True)),
            }
            for (day, group), row in rows[start : start + WRITE_BATCH]
        ]
        stmt = insert(DailyLoad).values(values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["athlete_id", "calendar_date", "sport_group"],
                set_={**{c: stmt.excluded[c] for c in VALUE_COLUMNS}, "updated_at": func.now()},
            )
        )


def _delete(session: Session, athlete_id: int, keys: Sequence[SeriesKey]) -> None:
    for start in range(0, len(keys), WRITE_BATCH):
        session.execute(
            delete(DailyLoad).where(
                DailyLoad.athlete_id == athlete_id,
                tuple_(DailyLoad.calendar_date, DailyLoad.sport_group).in_(keys[start : start + WRITE_BATCH]),
            )
        )
