"""The threshold timeline (`athlete_threshold`) and the resolver over it (ADR 0013).

Each row says: from `valid_from`, this value type for this sport group, from this source, is
`value`, until the next row with the same type, sport and source. Three sources:

- `manual`: the owner's input (`garminagent thresholds set`). Never rewritten by analytics.
- `garmin_zones`: max, resting and lactate threshold HR that Garmin's HR zones used, from the
  `athlete_zones` history. Zone sport DEFAULT applies to every group (`all`), RUNNING to `run`,
  CYCLING to `ride`; SWIMMING and ROWING map to no group and are left out.
- `activity`: values Garmin reported with each activity, on the activity's local start day.
  LTHR counts for the activity's own group. FTP counts only from rides (for `ride`) and
  threshold speed only from runs (for `run`): they are Garmin's cycling and running values,
  and carrying them to another sport would give a wrong load rather than none.

Resolving a date: per value type, the first source in PRECEDENCE that has a value on or before
that date wins; within a source, the group's own row beats an `all` row. A value holds until
the next change, so a manual value outranks later Garmin values until it is unset.
"""

import bisect
import logging
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final, Literal, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from garminagent.analytics.sports import SportGroup, sport_group
from garminagent.normalize import THRESHOLD_RANGES
from garminagent.storage.models import Activity, AthleteThreshold, AthleteZones

log = logging.getLogger(__name__)

type ValueType = Literal["ftp", "lthr", "max_hr", "resting_hr", "threshold_speed"]
type Source = Literal["manual", "garmin_zones", "activity"]
type ThresholdSport = Literal["all", "run", "ride", "ski", "strength", "other"]

VALUE_TYPES: Final[tuple[ValueType, ...]] = ("ftp", "lthr", "max_hr", "resting_hr", "threshold_speed")
SOURCES: Final[tuple[Source, ...]] = ("manual", "garmin_zones", "activity")
THRESHOLD_SPORTS: Final[tuple[ThresholdSport, ...]] = ("all", "run", "ride", "ski", "strength", "other")
PRECEDENCE: Final[tuple[Source, ...]] = ("manual", "garmin_zones", "activity")
DERIVED: Final[tuple[Source, ...]] = ("garmin_zones", "activity")

ZONE_SPORT_TO_THRESHOLD: Final[dict[str, ThresholdSport]] = {"DEFAULT": "all", "RUNNING": "run", "CYCLING": "ride"}
ZONE_COLUMNS: Final[dict[ValueType, str]] = {"max_hr": "max_hr", "resting_hr": "resting_hr", "lthr": "lthr"}
# Activity column per value type, and the groups whose activities may supply it (None: any).
ACTIVITY_COLUMNS: Final[dict[ValueType, tuple[str, frozenset[SportGroup] | None]]] = {
    "lthr": ("lactate_threshold_hr", None),
    "ftp": ("ftp_w", frozenset({"ride"})),
    "threshold_speed": ("threshold_speed_mps", frozenset({"run"})),
}


class ThresholdOutOfRange(ValueError):
    """A threshold outside THRESHOLD_RANGES. The message names the type and the range, never
    the rejected value."""


def check_range(value_type: ValueType, value: float) -> float:
    low, high = THRESHOLD_RANGES[value_type]
    if not low <= value <= high:
        raise ThresholdOutOfRange(f"{value_type} must be between {low:g} and {high:g}")
    return value


@dataclass(frozen=True, order=True)
class ThresholdRow:
    value_type: ValueType
    sport: ThresholdSport
    source: Source
    valid_from: date
    value: float


@dataclass(frozen=True)
class Resolved:
    value: float
    source: Source
    sport: ThresholdSport  # the row's sport: the group itself or `all`
    valid_from: date


type Thresholds = dict[ValueType, Resolved | None]


class ThresholdTimeline:
    """All of an athlete's threshold rows, indexed for resolving many dates (M6b resolves one
    per activity) without a query each."""

    def __init__(self, rows: Iterable[ThresholdRow]) -> None:
        series: dict[tuple[ValueType, ThresholdSport, Source], list[tuple[date, float]]] = defaultdict(list)
        for row in rows:
            series[(row.value_type, row.sport, row.source)].append((row.valid_from, row.value))
        self._days: dict[tuple[ValueType, ThresholdSport, Source], list[date]] = {}
        self._values: dict[tuple[ValueType, ThresholdSport, Source], list[float]] = {}
        for key, points in series.items():
            points.sort()
            self._days[key] = [d for d, _ in points]
            self._values[key] = [v for _, v in points]

    def at(self, day: date, group: ThresholdSport) -> Thresholds:
        sports: tuple[ThresholdSport, ...] = (group,) if group == "all" else (group, "all")
        result: Thresholds = {}
        for value_type in VALUE_TYPES:
            result[value_type] = self._first(value_type, day, sports)
        return result

    def _first(self, value_type: ValueType, day: date, sports: tuple[ThresholdSport, ...]) -> Resolved | None:
        for source in PRECEDENCE:
            for sport in sports:
                key = (value_type, sport, source)
                days = self._days.get(key)
                if not days:
                    continue
                i = bisect.bisect_right(days, day)
                if i:
                    return Resolved(self._values[key][i - 1], source, sport, days[i - 1])
        return None


def load_rows(session: Session, athlete_id: int, sources: Sequence[Source] = SOURCES) -> list[ThresholdRow]:
    rows = session.execute(
        select(
            AthleteThreshold.value_type,
            AthleteThreshold.sport,
            AthleteThreshold.source,
            AthleteThreshold.valid_from,
            AthleteThreshold.value,
        )
        .where(AthleteThreshold.athlete_id == athlete_id, AthleteThreshold.source.in_(sources))
        .order_by(AthleteThreshold.value_type, AthleteThreshold.sport, AthleteThreshold.valid_from)
    ).all()
    return [
        ThresholdRow(
            cast(ValueType, r.value_type), cast(ThresholdSport, r.sport), cast(Source, r.source), r.valid_from, r.value
        )
        for r in rows
    ]


def thresholds_at(session: Session, athlete_id: int, day: date, group: ThresholdSport) -> Thresholds:
    """The thresholds in effect on `day` for a sport group (see the module docstring)."""
    return ThresholdTimeline(load_rows(session, athlete_id)).at(day, group)


def set_manual(
    session: Session, athlete_id: int, value_type: ValueType, sport: ThresholdSport, valid_from: date, value: float
) -> None:
    """Add or replace the owner's value from `valid_from`. Range-checked first."""
    check_range(value_type, value)
    stmt = insert(AthleteThreshold).values(
        athlete_id=athlete_id, value_type=value_type, sport=sport, source="manual", valid_from=valid_from, value=value
    )
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_athlete_threshold_key", set_={"value": stmt.excluded.value, "updated_at": func.now()}
        )
    )


def unset_manual(
    session: Session, athlete_id: int, value_type: ValueType, sport: ThresholdSport, valid_from: date
) -> int:
    """Remove one manual row; returns rows removed (0 or 1)."""
    result = session.execute(
        delete(AthleteThreshold)
        .where(
            AthleteThreshold.athlete_id == athlete_id,
            AthleteThreshold.value_type == value_type,
            AthleteThreshold.sport == sport,
            AthleteThreshold.source == "manual",
            AthleteThreshold.valid_from == valid_from,
        )
        .returning(AthleteThreshold.id)
    )
    return len(result.all())


def _add_change(series: list[tuple[date, float]], day: date, value: float) -> None:
    """Append a change point. Only changes are kept; within one day the last value wins."""
    if series and series[-1][0] == day:
        series.pop()
    if not series or series[-1][1] != value:
        series.append((day, value))


@dataclass
class DerivedCounts:
    zones_sports_skipped: int = 0  # zone rows for SWIMMING or ROWING, which map to no group


def derive_from_zones(
    zone_rows: Iterable[tuple[str, date, int | None, int | None, int | None]], counts: DerivedCounts
) -> list[ThresholdRow]:
    """(sport, valid_from, max_hr, resting_hr, lthr) in any order -> `garmin_zones` rows. A NULL
    value isn't a change: the previous value holds."""
    series: dict[tuple[ValueType, ThresholdSport], list[tuple[date, float]]] = defaultdict(list)
    for zone_sport, valid_from, max_hr, resting_hr, lthr in sorted(zone_rows, key=lambda r: (r[1], r[0])):
        sport = ZONE_SPORT_TO_THRESHOLD.get(zone_sport)
        if sport is None:
            counts.zones_sports_skipped += 1
            continue
        for value_type, value in (("max_hr", max_hr), ("resting_hr", resting_hr), ("lthr", lthr)):
            if value is not None:
                _add_change(series[(cast(ValueType, value_type), sport)], valid_from, float(value))
    return [
        ThresholdRow(value_type, sport, "garmin_zones", day, value)
        for (value_type, sport), points in series.items()
        for day, value in points
    ]


@dataclass(frozen=True)
class ActivityThresholds:
    day: date
    sport_key: str | None
    lactate_threshold_hr: float | None
    threshold_speed_mps: float | None
    ftp_w: float | None


def derive_from_activities(activities: Iterable[ActivityThresholds]) -> list[ThresholdRow]:
    """Activities in start order -> `activity` rows (see the module docstring for which group
    each value counts for)."""
    series: dict[tuple[ValueType, ThresholdSport], list[tuple[date, float]]] = defaultdict(list)
    for activity in activities:
        group = sport_group(activity.sport_key)
        for value_type, (column, groups) in ACTIVITY_COLUMNS.items():
            value = cast(float | None, getattr(activity, column))
            if value is None or (groups is not None and group not in groups):
                continue
            _add_change(series[(value_type, group)], activity.day, value)
    return [
        ThresholdRow(value_type, sport, "activity", day, value)
        for (value_type, sport), points in series.items()
        for day, value in points
    ]


def _activity_inputs(session: Session, athlete_id: int) -> list[ActivityThresholds]:
    rows = session.execute(
        select(
            Activity.start_time_utc,
            Activity.start_time_local,
            Activity.sport,
            Activity.lactate_threshold_hr,
            Activity.threshold_speed_mps,
            Activity.ftp_w,
        )
        .where(
            Activity.athlete_id == athlete_id,
            (Activity.lactate_threshold_hr.is_not(None))
            | (Activity.threshold_speed_mps.is_not(None))
            | (Activity.ftp_w.is_not(None)),
        )
        .order_by(Activity.start_time_utc, Activity.activity_id)
    ).all()
    return [
        ActivityThresholds(
            # The local day when known, else UTC (ADR 0013: days come from start_time_local).
            day=(r.start_time_local or r.start_time_utc).date(),
            sport_key=r.sport,
            lactate_threshold_hr=r.lactate_threshold_hr,
            threshold_speed_mps=r.threshold_speed_mps,
            ftp_w=r.ftp_w,
        )
        for r in rows
    ]


type _Key = tuple[ValueType, ThresholdSport, Source, date]


@dataclass(frozen=True)
class RebuildResult:
    rows: dict[Source, int]  # rows per derived source after the rebuild
    inserted: int
    updated: int
    deleted: int
    zones_sports_skipped: int


def rebuild_derived(session: Session, athlete_id: int) -> RebuildResult:
    """Bring the `garmin_zones` and `activity` rows in line with their inputs, touching only rows
    that differ. Manual rows are never read or written here."""
    counts = DerivedCounts()
    zone_rows = session.execute(
        select(
            AthleteZones.sport, AthleteZones.valid_from, AthleteZones.max_hr, AthleteZones.resting_hr, AthleteZones.lthr
        ).where(AthleteZones.athlete_id == athlete_id)
    ).all()
    desired_rows = derive_from_zones(
        [(r.sport, r.valid_from, r.max_hr, r.resting_hr, r.lthr) for r in zone_rows], counts
    )
    desired_rows += derive_from_activities(_activity_inputs(session, athlete_id))
    desired: dict[_Key, float] = {(r.value_type, r.sport, r.source, r.valid_from): r.value for r in desired_rows}
    existing: dict[_Key, float] = {
        (r.value_type, r.sport, r.source, r.valid_from): r.value for r in load_rows(session, athlete_id, DERIVED)
    }

    stale = [key for key in existing if key not in desired]
    for value_type, sport, source, valid_from in stale:
        session.execute(
            delete(AthleteThreshold).where(
                AthleteThreshold.athlete_id == athlete_id,
                AthleteThreshold.value_type == value_type,
                AthleteThreshold.sport == sport,
                AthleteThreshold.source == source,
                AthleteThreshold.valid_from == valid_from,
            )
        )
    changed = [(key, value) for key, value in desired.items() if key in existing and existing[key] != value]
    for (value_type, sport, source, valid_from), value in changed:
        session.execute(
            update(AthleteThreshold)
            .where(
                AthleteThreshold.athlete_id == athlete_id,
                AthleteThreshold.value_type == value_type,
                AthleteThreshold.sport == sport,
                AthleteThreshold.source == source,
                AthleteThreshold.valid_from == valid_from,
            )
            .values(value=value)
        )
    new = [
        {
            "athlete_id": athlete_id,
            "value_type": value_type,
            "sport": sport,
            "source": source,
            "valid_from": valid_from,
            "value": value,
        }
        for (value_type, sport, source, valid_from), value in desired.items()
        if (value_type, sport, source, valid_from) not in existing
    ]
    if new:
        session.execute(insert(AthleteThreshold), new)
    per_source: dict[Source, int] = dict.fromkeys(DERIVED, 0)
    for _, _, source, _ in desired:
        per_source[source] += 1
    return RebuildResult(per_source, len(new), len(changed), len(stale), counts.zones_sports_skipped)
