"""Re-parse stored responses into the normalized tables, with no Garmin requests.

Used when parsing changes: new columns (M6a's thresholds on `activity`) or new range checks
(ADR 0015) reach history without re-fetching it. A record can appear in many stored responses
(re-fetched days, overlapping pages); only its newest version is used, the same one a sync
would have written last. Records are only inserted or updated, never deleted, as in a sync.
"""

import logging
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.orm import Session, sessionmaker

from garminagent.normalize import (
    Normalized,
    parse_activities,
    parse_daily_summaries,
    parse_hrv,
    parse_sleep,
    parse_weight,
)
from garminagent.storage.repository import (
    iter_raw_payloads,
    upsert_activities,
    upsert_daily_summaries,
    upsert_hrv,
    upsert_sleep,
    upsert_weight,
)
from garminagent.sync import ACTIVITIES, DAILY_SUMMARIES, HRV, SLEEP, WEIGHT

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Dataset:
    parse: Callable[[Any], Normalized[Any]]
    upsert: Callable[[Session, int, list[Any], int | None], int]
    key: Callable[[Any], Hashable]


def _by_date(record: Any) -> Hashable:
    return record.calendar_date


# Each dataset's raw_payload endpoint is its sync dataset name.
DATASETS: Final[dict[str, _Dataset]] = {
    ACTIVITIES: _Dataset(parse_activities, upsert_activities, lambda r: r.activity_id),
    DAILY_SUMMARIES: _Dataset(parse_daily_summaries, upsert_daily_summaries, _by_date),
    SLEEP: _Dataset(parse_sleep, upsert_sleep, _by_date),
    HRV: _Dataset(parse_hrv, upsert_hrv, _by_date),
    WEIGHT: _Dataset(parse_weight, upsert_weight, _by_date),
}


def renormalize(sessions: sessionmaker[Session], athlete_id: int, dataset: str) -> dict[str, int]:
    """Rewrite a dataset's normalized rows from the newest stored version of each record, in one
    transaction. Returns counts: payloads read, records written, older versions ignored, and
    items skipped by validation."""
    spec = DATASETS[dataset]
    counts = {"payloads": 0, "records": 0, "superseded": 0, "skipped": 0}
    seen: set[Hashable] = set()
    with sessions.begin() as session:
        for stored in iter_raw_payloads(session, athlete_id, dataset):
            parsed = spec.parse(stored.payload)
            fresh: list[Any] = []
            # Within one response the last item per key wins, as in a sync's upsert.
            for record in reversed(parsed.records):
                key = spec.key(record)
                if key in seen:
                    counts["superseded"] += 1
                    continue
                seen.add(key)
                fresh.append(record)
            fresh.reverse()
            spec.upsert(session, athlete_id, fresh, stored.id)
            counts["payloads"] += 1
            counts["records"] += len(fresh)
            counts["skipped"] += parsed.skipped
    log.info(
        "renormalize %s: %d records from %d stored responses (%d older versions ignored)",
        dataset,
        counts["records"],
        counts["payloads"],
        counts["superseded"],
    )
    return counts
