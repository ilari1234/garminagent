"""Keeps `daily_recovery` current: HRV and resting HR trends against their own baselines, with
flags (ADR 0013, M6e, and the owner's decisions on M6e).

**Trends, not diagnoses.** A flag says the last week sits outside the athlete's usual range;
it never says why (illness, travel, alcohol, a hard block, a new watch). Readers must not treat
it as more than that, and no training limit is attached to it here (limits are phase 3's,
OPEN_QUESTIONS 11).

**Full recompute, minimal writes,** as `daily_load` does: the series is rebuilt from scratch
every run (one row a day), compared with what is stored, and only differing rows are written;
rows outside the new series are deleted. So `updated_at` means "last changed".

**Every day with input around it.** The series runs from the first day with an HRV or resting
HR value to the last such day, so a reader can tell a missing night (counts below the minimum,
values NULL) from a day outside the data.

**Definitions** (change one and bump ALGO_VERSION):

- HRV is `hrv_summary.last_night_avg` (ms), Garmin's average over the night, taken as ln(ms):
  HRV is right-skewed, and the log makes the SD band symmetric. Resting HR is
  `daily_summary.resting_hr` (bpm), as is.
- Recent: the mean over the 7 days ending on the day (the day and the 6 before it), with at
  least 4 days present; otherwise NULL.
- Baseline: the mean and sample SD of the single days in the 60 days before the recent window
  (days -66 to -7), with at least 30 days present; otherwise NULL. The baseline never overlaps
  the week being judged, so that week can't pull its own baseline along.
- HRV and resting HR are judged separately: a missing HRV night never hides the resting HR flag.
- Flag: `below` under baseline mean - 0.5 SD, `above` over mean + 0.5 SD, else `within`
  (a value on the boundary is `within`). The SD is of single days, not of 7-day means (owner's
  decision, 2026-11-24): on steady synthetic nights (a floor; real series drift and flag more
  often) this flags about one day in five and still
  catches a sustained 10 % HRV drop within 2 to 9 days, while ±0.5 SD of 7-day means flagged
  about two days in three with nothing changing. The 0.5 factor is not yet verified against a
  primary source (OPEN_QUESTIONS 31). Low HRV and high resting HR are the directions that
  usually go with poor recovery; the flags stay neutral and name the direction only. With a
  very steady resting HR (a baseline SD under 1 bpm), a 1 bpm change, the smallest step a
  whole-bpm value can take, can flip a flag.
"""

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import date, timedelta
from typing import Any, Final, Literal

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from garminagent.storage.models import DailyRecovery, DailySummary, HrvSummary

log = logging.getLogger(__name__)

ALGO_VERSION: Final = 1
RECENT_DAYS: Final = 7
BASELINE_DAYS: Final = 60
MIN_RECENT_DAYS: Final = 4
MIN_BASELINE_DAYS: Final = 30
BAND_SD: Final = 0.5  # half-width of the "within" band, in baseline SDs (OPEN_QUESTIONS 31)
# Rows per INSERT: 15 columns each stays well under Postgres's 65535 bind parameters.
WRITE_BATCH: Final = 1000

type Trend = Literal["below", "within", "above"]
TRENDS: Final[tuple[Trend, ...]] = ("below", "within", "above")


@dataclass(frozen=True)
class Window:
    """One measure on one day: the recent mean and the baseline it is judged against. `days`
    and `baseline_days` count days with a value in each window. Means and SD are None below the
    minimums; `trend` is None unless both are present."""

    days: int
    mean: float | None
    baseline_days: int
    baseline_mean: float | None
    baseline_sd: float | None
    trend: Trend | None


@dataclass(frozen=True)
class DayRecovery:
    """One `daily_recovery` row without its keys; field order is the comparison order and
    matches VALUE_COLUMNS."""

    hrv_days: int
    hrv_ln_mean: float | None
    hrv_baseline_days: int
    hrv_baseline_ln_mean: float | None
    hrv_baseline_ln_sd: float | None
    hrv_trend: Trend | None
    rhr_days: int
    rhr_mean: float | None
    rhr_baseline_days: int
    rhr_baseline_mean: float | None
    rhr_baseline_sd: float | None
    rhr_trend: Trend | None
    algo_version: int = ALGO_VERSION

    @classmethod
    def of(cls, hrv: Window, rhr: Window) -> DayRecovery:
        return cls(*astuple(hrv), *astuple(rhr))


def recent(values: Mapping[date, float], day: date) -> tuple[int, float | None]:
    """Days with a value in the 7 days ending on `day`, and their mean if there are enough."""
    found = [v for n in range(RECENT_DAYS) if (v := values.get(day - timedelta(days=n))) is not None]
    return len(found), math.fsum(found) / len(found) if len(found) >= MIN_RECENT_DAYS else None


def window(values: Mapping[date, float], day: date) -> Window:
    """The recent mean and baseline for `day` from one measure's values by date. Pure."""
    days, mean = recent(values, day)
    baseline_end = day - timedelta(days=RECENT_DAYS)
    baseline = [v for n in range(BASELINE_DAYS) if (v := values.get(baseline_end - timedelta(days=n))) is not None]
    base_mean = base_sd = None
    if len(baseline) >= MIN_BASELINE_DAYS:
        base_mean = math.fsum(baseline) / len(baseline)
        # Sample SD (n - 1); fsum keeps it independent of summation order.
        base_sd = math.sqrt(math.fsum((v - base_mean) ** 2 for v in baseline) / (len(baseline) - 1))
    return Window(days, mean, len(baseline), base_mean, base_sd, _trend(mean, base_mean, base_sd))


def _trend(mean: float | None, base_mean: float | None, base_sd: float | None) -> Trend | None:
    if mean is None or base_mean is None or base_sd is None:
        return None
    if mean < base_mean - BAND_SD * base_sd:
        return "below"
    if mean > base_mean + BAND_SD * base_sd:
        return "above"
    return "within"


def build_series(hrv_ms: Mapping[date, int], resting_hr: Mapping[date, int]) -> dict[date, DayRecovery]:
    """A row per day from the first to the last day with either input. An HRV of 0 ms has no
    log and is dropped (parsing range-checks it away already). Pure."""
    ln_hrv = {d: math.log(v) for d, v in hrv_ms.items() if v > 0}
    rhr = {d: float(v) for d, v in resting_hr.items()}
    days = ln_hrv.keys() | rhr.keys()
    if not days:
        return {}
    first, last = min(days), max(days)
    return {
        day: DayRecovery.of(window(ln_hrv, day), window(rhr, day))
        for day in (first + timedelta(days=n) for n in range((last - first).days + 1))
    }


@dataclass
class DailyRecoveryCounts:
    days: int = 0
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    hrv_days: int = 0  # days with a recent HRV mean
    rhr_days: int = 0
    hrv_flagged_days: int = 0  # days with an HRV trend (recent mean and baseline)
    rhr_flagged_days: int = 0


def update_daily_recovery(sessions: sessionmaker[Session], athlete_id: int) -> DailyRecoveryCounts:
    """Rebuild the series and write the differences, in one transaction."""
    with sessions.begin() as session:
        series = build_series(
            _by_date(session, HrvSummary.last_night_avg, HrvSummary, athlete_id),
            _by_date(session, DailySummary.resting_hr, DailySummary, athlete_id),
        )
        stored = _stored(session, athlete_id)
        changed = [day for day, row in series.items() if not _same(stored.get(day), row)]
        gone = [day for day in stored if day not in series]
        _upsert(session, athlete_id, [(day, series[day]) for day in changed])
        _delete(session, athlete_id, gone)
    inserted = sum(day not in stored for day in changed)
    rows = series.values()
    return DailyRecoveryCounts(
        days=len(series),
        inserted=inserted,
        updated=len(changed) - inserted,
        deleted=len(gone),
        hrv_days=sum(r.hrv_ln_mean is not None for r in rows),
        rhr_days=sum(r.rhr_mean is not None for r in rows),
        hrv_flagged_days=sum(r.hrv_trend is not None for r in rows),
        rhr_flagged_days=sum(r.rhr_trend is not None for r in rows),
    )


def _by_date(session: Session, column: Any, model: Any, athlete_id: int) -> dict[date, int]:
    return {
        d: v
        for d, v in session.execute(
            select(model.calendar_date, column).where(model.athlete_id == athlete_id, column.is_not(None))
        )
    }


VALUE_COLUMNS: Final = tuple(f.name for f in fields(DayRecovery))
# Floats round-trip float8 exactly with Postgres's default `extra_float_digits`; the tolerance
# keeps a server setting that rounds them from rewriting every row on every run (as in M6c).
REL_TOL: Final = 1e-12


def _same(stored: DayRecovery | None, row: DayRecovery) -> bool:
    if stored is None:
        return False
    for a, b in zip(astuple(stored), astuple(row), strict=True):
        if isinstance(a, float) and isinstance(b, float):
            if not math.isclose(a, b, rel_tol=REL_TOL, abs_tol=REL_TOL):
                return False
        elif a != b:
            return False
    return True


def _stored(session: Session, athlete_id: int) -> dict[date, DayRecovery]:
    columns = [getattr(DailyRecovery, name) for name in VALUE_COLUMNS]
    return {
        r[0]: DayRecovery(*r[1:])
        for r in session.execute(
            select(DailyRecovery.calendar_date, *columns).where(DailyRecovery.athlete_id == athlete_id)
        )
    }


def _batches[T](items: Sequence[T]) -> list[Sequence[T]]:
    return [items[start : start + WRITE_BATCH] for start in range(0, len(items), WRITE_BATCH)]


def _upsert(session: Session, athlete_id: int, rows: Sequence[tuple[date, DayRecovery]]) -> None:
    for batch in _batches(rows):
        values: list[dict[str, Any]] = [
            {"athlete_id": athlete_id, "calendar_date": day, **dict(zip(VALUE_COLUMNS, astuple(row), strict=True))}
            for day, row in batch
        ]
        stmt = insert(DailyRecovery).values(values)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["athlete_id", "calendar_date"],
                set_={**{c: stmt.excluded[c] for c in VALUE_COLUMNS}, "updated_at": func.now()},
            )
        )


def _delete(session: Session, athlete_id: int, days: Sequence[date]) -> None:
    for batch in _batches(days):
        session.execute(
            delete(DailyRecovery).where(DailyRecovery.athlete_id == athlete_id, DailyRecovery.calendar_date.in_(batch))
        )
