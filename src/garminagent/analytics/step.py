"""The `analyze` step: runs after FIT parsing inside the locked sync run, or alone through
`garminagent analyze`. Makes no Garmin requests.

In M6a it groups activities by sport and rebuilds the derived threshold timeline; the metrics
come in M6b. It never fails the sync: each part runs in its own transaction, and an error in
one becomes an `errors` count and a log line naming the exception class, never its message
(which may quote data). Counts go to `sync_run.counts["analytics"]`.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from garminagent.analytics.sports import GROUPS, TYPE_KEY, is_mapped, sport_group
from garminagent.analytics.thresholds import load_rows, rebuild_derived
from garminagent.storage.models import Activity

log = logging.getLogger(__name__)

ANALYTICS = "analytics"


@dataclass
class AnalyticsResult:
    counts: dict[str, int] = field(default_factory=dict[str, int])
    # Garmin type keys not in SPORT_GROUPS, so they can be added. Shown by `garminagent analyze`
    # only; `sync_run.counts` holds integers alone.
    unmapped_keys: list[str] = field(default_factory=list[str])

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def analyze(sessions: sessionmaker[Session], athlete_id: int) -> AnalyticsResult:
    """Run every analytics part; never raises."""
    result = AnalyticsResult()
    parts = {"sport_groups": _sport_groups, "thresholds": _thresholds}
    for name, part in parts.items():
        try:
            part(sessions, athlete_id, result)
        except Exception as exc:
            # Includes database errors: analytics must not decide how a sync run ends.
            log.error("analyze: %s failed: %s", name, exc.__class__.__name__)
            result.add("errors")
    log.info("analyze: %s", result.counts)
    return result


def run_analytics(sessions: sessionmaker[Session], athlete_id: int) -> dict[str, int]:
    """The counts for `sync_run.counts["analytics"]`."""
    return analyze(sessions, athlete_id).counts


def _sport_groups(sessions: sessionmaker[Session], athlete_id: int, result: AnalyticsResult) -> None:
    with sessions() as session:
        rows = session.execute(
            select(Activity.sport, func.count()).where(Activity.athlete_id == athlete_id).group_by(Activity.sport)
        ).all()
    per_group = dict.fromkeys(GROUPS, 0)
    unmapped: set[str] = set()
    unmapped_activities = 0
    for key, n in rows:
        per_group[sport_group(key)] += n
        if not is_mapped(key):
            unmapped_activities += n
            if key is not None and TYPE_KEY.fullmatch(key):
                unmapped.add(key)
    result.add("activities", sum(per_group.values()))
    for group, n in per_group.items():
        result.add(f"sport_{group}", n)
    result.add("unmapped_activities", unmapped_activities)
    result.add("unmapped_sport_keys", len(unmapped))
    result.unmapped_keys = sorted(unmapped)
    if unmapped:
        log.info("analyze: %d activities have %d sport keys not yet grouped", unmapped_activities, len(unmapped))


def _thresholds(sessions: sessionmaker[Session], athlete_id: int, result: AnalyticsResult) -> None:
    with sessions.begin() as session:
        rebuilt = rebuild_derived(session, athlete_id)
        manual = len(load_rows(session, athlete_id, ("manual",)))
    result.add("thresholds_manual", manual)
    for source, n in rebuilt.rows.items():
        result.add(f"thresholds_{source}", n)
    result.add("thresholds_inserted", rebuilt.inserted)
    result.add("thresholds_updated", rebuilt.updated)
    result.add("thresholds_deleted", rebuilt.deleted)
    if rebuilt.zones_sports_skipped:
        result.add("zones_sports_skipped", rebuilt.zones_sports_skipped)
