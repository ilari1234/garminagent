"""The `analyze` step: runs after FIT parsing inside the locked sync run, or alone through
`garminagent analyze`. Makes no Garmin requests.

It groups activities by sport, rebuilds the derived threshold timeline (M6a) and brings the
per-activity metrics up to date (M6b). It never fails the sync: each part runs in its own transaction, and an error in
one becomes an `errors` count and a log line naming the exception class, never its message
(which may quote data). Counts go to `sync_run.counts["analytics"]`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from garminagent.analytics.activity_metrics import update_metrics
from garminagent.analytics.sports import GROUPS, TYPE_KEY, is_mapped, sport_group
from garminagent.analytics.thresholds import load_rows, rebuild_derived
from garminagent.config import DEFAULT_ANALYTICS_MAX_ACTIVITIES, Settings
from garminagent.storage.models import Activity

log = logging.getLogger(__name__)

ANALYTICS = "analytics"


@dataclass(frozen=True)
class AnalyticsOptions:
    """From settings: the HR load coefficient (None: HR load isn't computed) and the per-run
    cap on metrics recomputed (None: no cap, `analyze --full`)."""

    trimp_b: float | None = field(default=None, repr=False)  # implies sex: never in a repr
    max_activities: int | None = DEFAULT_ANALYTICS_MAX_ACTIVITIES

    @classmethod
    def from_settings(cls, settings: Settings, *, full: bool = False) -> AnalyticsOptions:
        return cls(settings.analytics_trimp_b, None if full else settings.analytics_max_activities)


@dataclass
class AnalyticsResult:
    counts: dict[str, int] = field(default_factory=dict[str, int])
    # Garmin type keys not in SPORT_GROUPS, so they can be added. Shown by `garminagent analyze`
    # only; `sync_run.counts` holds integers alone.
    unmapped_keys: list[str] = field(default_factory=list[str])

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def analyze(
    sessions: sessionmaker[Session], athlete_id: int, options: AnalyticsOptions | None = None
) -> AnalyticsResult:
    """Run every analytics part, in order (metrics resolve against the rebuilt timeline);
    never raises."""
    options = options or AnalyticsOptions()
    result = AnalyticsResult()
    if options.trimp_b is None:
        log.warning("analyze: ANALYTICS_TRIMP_B is not set, so HR load is not computed")
    parts: dict[str, Callable[[sessionmaker[Session], int, AnalyticsResult, AnalyticsOptions], None]] = {
        "sport_groups": _sport_groups,
        "thresholds": _thresholds,
        "activity_metrics": _activity_metrics,
    }
    for name, part in parts.items():
        try:
            part(sessions, athlete_id, result, options)
        except Exception as exc:
            # Includes database errors: analytics must not decide how a sync run ends.
            log.error("analyze: %s failed: %s", name, exc.__class__.__name__)
            result.add("errors")
    log.info("analyze: %s", result.counts)
    return result


def run_analytics(
    sessions: sessionmaker[Session], athlete_id: int, options: AnalyticsOptions | None = None
) -> dict[str, int]:
    """The counts for `sync_run.counts["analytics"]`."""
    return analyze(sessions, athlete_id, options).counts


def _sport_groups(
    sessions: sessionmaker[Session], athlete_id: int, result: AnalyticsResult, options: AnalyticsOptions
) -> None:
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


def _thresholds(
    sessions: sessionmaker[Session], athlete_id: int, result: AnalyticsResult, options: AnalyticsOptions
) -> None:
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


def _activity_metrics(
    sessions: sessionmaker[Session], athlete_id: int, result: AnalyticsResult, options: AnalyticsOptions
) -> None:
    counts = update_metrics(sessions, athlete_id, options.trimp_b, options.max_activities)
    result.add("metrics_computed", counts.computed)
    result.add("metrics_current", counts.current)
    result.add("metrics_pending", counts.pending)
    result.add("metrics_no_samples", counts.no_samples)
    result.add("metrics_backfilled", counts.backfilled)
    result.add("hr_load_not_configured", counts.hr_load_not_configured)
    for method in ("power", "hr", "none"):
        result.add(f"load_{method}", (counts.load_methods or {}).get(method, 0))
    if counts.errors:
        result.add("errors", counts.errors)
