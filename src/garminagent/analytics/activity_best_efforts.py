"""Keeps `activity_best_effort` current: one row per activity, kind and span (ADR 0013, M6d).

**Recompute by input fingerprint**, as in M6b, but with fewer inputs: best efforts use no
thresholds, so the fingerprint covers the activity's day and sport group, its FIT files
(content hash, parse status and parser version, which decide what `activity_sample` holds) and
BEST_EFFORT_ALGO_VERSION. Every row of an activity carries the same fingerprint, and an
activity's rows are replaced together, so one stored fingerprint per activity tells whether it
is current. Every activity has rows (every group has power windows), so an activity is never
recomputed just because it has nothing to show.

**Capped per run** by the same setting as the metrics, newest activities first; the rest are
counted as pending. `analyze --full` passes no cap.
"""

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session, sessionmaker

from garminagent.analytics.activity_metrics import ActivityInput, activity_inputs
from garminagent.analytics.best_efforts import BEST_EFFORT_ALGO_VERSION, BestEfforts, compute
from garminagent.analytics.metrics import Point
from garminagent.storage.models import ActivityBestEffort, ActivitySample

log = logging.getLogger(__name__)

# Activities per transaction, as in M6b: bounds memory (samples are loaded per batch) and how
# much one failure rolls back.
BATCH: Final = 25


def fingerprint(activity: ActivityInput) -> str:
    inputs = {
        "algo": BEST_EFFORT_ALGO_VERSION,
        "day": activity.day.isoformat(),
        "group": activity.group,
        "files": [list(f) for f in sorted(activity.fit_files, key=lambda f: f[0])],
    }
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def row_values(activity: ActivityInput, efforts: BestEfforts, digest: str) -> list[dict[str, Any]]:
    return [
        {
            "activity_id": activity.activity_id,
            "kind": e.kind,
            "span": e.span,
            "activity_date": activity.day,
            "sport_group": activity.group,
            "seconds": e.seconds,
            "power_w": e.power_w,
            "algo_version": BEST_EFFORT_ALGO_VERSION,
            "input_fingerprint": digest,
        }
        for e in efforts.efforts
    ]


@dataclass
class BestEffortCounts:
    activities: int = 0
    current: int = 0
    computed: int = 0
    pending: int = 0
    with_distance: int = 0  # computed this run with at least one distance effort
    with_power: int = 0  # computed this run with at least one power effort
    spikes: int = 0  # this run
    counter_drops: int = 0  # this run
    errors: int = 0


def update_best_efforts(
    sessions: sessionmaker[Session], athlete_id: int, max_activities: int | None
) -> BestEffortCounts:
    counts = BestEffortCounts()
    with sessions() as session:
        activities = activity_inputs(session, athlete_id)
        stored: dict[int, set[str]] = defaultdict(set)
        for r in session.execute(
            select(ActivityBestEffort.activity_id, ActivityBestEffort.input_fingerprint)
            .where(ActivityBestEffort.athlete_id == athlete_id)
            .distinct()
        ):
            stored[r.activity_id].add(r.input_fingerprint)
    stale: list[tuple[ActivityInput, str]] = []
    for activity in activities:  # newest first
        digest = fingerprint(activity)
        if stored.get(activity.activity_id) != {digest}:
            stale.append((activity, digest))
    counts.activities = len(activities)
    counts.current = len(activities) - len(stale)
    todo = stale if max_activities is None else stale[:max_activities]
    counts.pending = len(stale) - len(todo)

    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        with sessions.begin() as session:
            points = _points(session, athlete_id, [a.activity_id for a, _ in batch])
            rows: list[dict[str, Any]] = []
            done: list[int] = []
            for activity, digest in batch:
                try:
                    efforts = compute(points.get(activity.activity_id, []), activity.group)
                except Exception as exc:
                    # One activity's arithmetic must not stop the others. Class name only: a
                    # message could quote values.
                    log.error("analyze: best efforts for one activity failed: %s", exc.__class__.__name__)
                    counts.errors += 1
                    continue
                rows.extend({"athlete_id": athlete_id, **r} for r in row_values(activity, efforts, digest))
                done.append(activity.activity_id)
                counts.computed += 1
                counts.with_distance += any(e.seconds is not None for e in efforts.efforts)
                counts.with_power += any(e.power_w is not None for e in efforts.efforts)
                counts.spikes += efforts.spikes
                counts.counter_drops += efforts.counter_drops
            _replace(session, athlete_id, done, rows)
    return counts


def _points(session: Session, athlete_id: int, activity_ids: Sequence[int]) -> dict[int, list[Point]]:
    points: dict[int, list[Point]] = defaultdict(list)
    for r in session.execute(
        select(
            ActivitySample.activity_id,
            ActivitySample.elapsed_s,
            ActivitySample.power_w,
            ActivitySample.distance_m,
        ).where(ActivitySample.athlete_id == athlete_id, ActivitySample.activity_id.in_(activity_ids))
    ):
        points[r.activity_id].append(Point(r.elapsed_s, None, r.power_w, None, r.distance_m))
    return points


def _replace(session: Session, athlete_id: int, activity_ids: list[int], rows: list[dict[str, Any]]) -> None:
    """Replace the activities' rows together: the group (and so the set of spans) can change."""
    if not activity_ids:
        return
    session.execute(
        delete(ActivityBestEffort).where(
            ActivityBestEffort.athlete_id == athlete_id, ActivityBestEffort.activity_id.in_(activity_ids)
        )
    )
    if rows:
        session.execute(insert(ActivityBestEffort), rows)
