"""Garmin's HR zone floors in effect on a date, from the `athlete_zones` history (ADR 0013).

The group's own zone sport (RUNNING for runs, CYCLING for rides) beats DEFAULT, and the latest
row on or before the date holds. Only rows with all five floors count: zones that can't be
used as a whole aren't used in part, and a row missing floors is not a change. For a date
before the first usable row, the earliest one holds (the backward fill the owner decided for
thresholds, ADR 0013), marked as filled: zone history starts with the first fetch.
"""

import bisect
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from garminagent.analytics.sports import SportGroup
from garminagent.storage.models import AthleteZones

ZONE_SPORT_FOR_GROUP: Final[dict[SportGroup, str]] = {"run": "RUNNING", "ride": "CYCLING"}
DEFAULT_ZONE_SPORT: Final = "DEFAULT"

type Floors = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class ZoneFloors:
    zone_sport: str
    valid_from: date
    floors: Floors  # bpm, ascending: zone k starts at floors[k - 1]
    backfilled: bool = False


@dataclass(frozen=True)
class ZoneRow:
    sport: str
    valid_from: date
    floors: tuple[int | None, int | None, int | None, int | None, int | None]


class ZoneHistory:
    def __init__(self, rows: Iterable[ZoneRow]) -> None:
        series: dict[str, list[tuple[date, Floors]]] = defaultdict(list)
        for row in rows:
            f1, f2, f3, f4, f5 = row.floors
            if f1 is None or f2 is None or f3 is None or f4 is None or f5 is None:
                continue
            series[row.sport].append((row.valid_from, (f1, f2, f3, f4, f5)))
        self._days: dict[str, list[date]] = {}
        self._floors: dict[str, list[Floors]] = {}
        for sport, points in series.items():
            points.sort()
            self._days[sport] = [d for d, _ in points]
            self._floors[sport] = [f for _, f in points]

    def at(self, day: date, group: SportGroup) -> ZoneFloors | None:
        own = ZONE_SPORT_FOR_GROUP.get(group)
        sports = (own, DEFAULT_ZONE_SPORT) if own else (DEFAULT_ZONE_SPORT,)
        for sport in sports:
            days = self._days.get(sport)
            if days and (i := bisect.bisect_right(days, day)):
                return ZoneFloors(sport, days[i - 1], self._floors[sport][i - 1])
        for sport in sports:
            days = self._days.get(sport)
            if days:
                return ZoneFloors(sport, days[0], self._floors[sport][0], backfilled=True)
        return None


def load_zone_history(session: Session, athlete_id: int) -> ZoneHistory:
    rows = session.execute(
        select(
            AthleteZones.sport,
            AthleteZones.valid_from,
            AthleteZones.zone1_floor,
            AthleteZones.zone2_floor,
            AthleteZones.zone3_floor,
            AthleteZones.zone4_floor,
            AthleteZones.zone5_floor,
        ).where(AthleteZones.athlete_id == athlete_id)
    ).all()
    return ZoneHistory(
        ZoneRow(r.sport, r.valid_from, (r.zone1_floor, r.zone2_floor, r.zone3_floor, r.zone4_floor, r.zone5_floor))
        for r in rows
    )
