"""Running sync operationally: one locked, recorded run; the scheduling loop; status.

`run_sync_once` is what both `garminagent sync` (one-shot: cron, ECS scheduled tasks) and
`garminagent schedule` (long-running: the local compose scheduler) execute. Runs are
serialized with a Postgres advisory lock, so a manual run during a scheduled one, or two
overlapping scheduled tasks, can't sync twice at once; the second one skips.

Everything recorded or reported here is counts, dates and stop reasons, never data values.
"""

import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from garminagent.config import Settings
from garminagent.fit import PARSER_VERSION
from garminagent.garmin import GarminAuthError, GarminClient
from garminagent.storage.blobs import make_blob_store
from garminagent.storage.db import make_engine, make_session_factory, sync_lock
from garminagent.storage.models import Athlete
from garminagent.storage.repository import (
    all_cursors,
    count_activities_without_file,
    count_fit_files_to_parse,
    ensure_athlete,
    last_sync_run,
    record_sync_run,
)
from garminagent.sync import DEFAULT_HISTORY_DAYS, INTERNAL_ERROR, Syncer, SyncReport

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TEMPFAIL = 75  # sysexits.h EX_TEMPFAIL: rate limited, try again later

ALREADY_RUNNING = "already_running"
AUTH_FAILED = "auth_failed"
DATABASE_ERROR = "database_error"
# Stops that mean something is wrong (as opposed to "come back later").
FAILURES = {"garmin_error", AUTH_FAILED, DATABASE_ERROR, INTERNAL_ERROR}
RATE_LIMIT_PAUSE_S = 2 * 3600


@dataclass(frozen=True)
class RunOutcome:
    stopped: str | None  # None = complete; else a SyncReport stop reason or one of the above
    report: SyncReport | None
    exit_code: int


def run_sync_once(
    settings: Settings,
    *,
    since: date | None = None,
    max_requests: int | None = None,
) -> RunOutcome:
    today = datetime.now(UTC).date()
    start = since or settings.sync_start_date or today - timedelta(days=DEFAULT_HISTORY_DAYS)
    engine = make_engine(settings.database_url.get_secret_value())
    sessions = make_session_factory(engine)
    started_at = datetime.now(UTC)
    try:
        with sync_lock(engine) as acquired:
            if not acquired:
                log.info("another sync is running; skipping this one")
                return RunOutcome(ALREADY_RUNNING, None, EXIT_OK)
            try:
                with GarminClient.from_settings(settings) as client:
                    report = Syncer(
                        client,
                        sessions,
                        make_blob_store(settings),
                        start=start,
                        today=today,
                        max_requests=max_requests or settings.sync_max_requests,
                        refetch_days=settings.sync_refetch_days,
                        activity_refetch_days=settings.sync_activity_refetch_days,
                    ).run()
            except GarminAuthError as exc:
                log.error("Garmin authentication failed: %s", exc)
                report = SyncReport(stopped=AUTH_FAILED)
            with sessions.begin() as session:
                record_sync_run(
                    session,
                    ensure_athlete(session),
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    stopped=report.stopped,
                    requests=report.requests,
                    counts=report.counts,
                )
    except SQLAlchemyError as exc:
        log.error("sync failed: database error %s", exc.__class__.__name__)
        return RunOutcome(DATABASE_ERROR, None, EXIT_ERROR)
    except Exception as exc:
        # Outside what Syncer.run already catches (e.g. building the client): fail this run with
        # a code, never a traceback. Class name only: the message may quote data.
        log.error("sync failed: unexpected error %s", exc.__class__.__name__)
        _record_failed_run(sessions, started_at, INTERNAL_ERROR)
        return RunOutcome(INTERNAL_ERROR, None, EXIT_ERROR)
    finally:
        engine.dispose()
    if report.stopped == "rate_limited":
        code = EXIT_TEMPFAIL
    elif report.stopped in FAILURES:
        code = EXIT_ERROR
    else:
        code = EXIT_OK
    return RunOutcome(report.stopped, report, code)


def _record_failed_run(sessions: sessionmaker[Session], started_at: datetime, stopped: str) -> None:
    """Record a run that failed outside the sync itself, so `status` and the health check see it
    instead of the last good run. Best effort: if the database is what failed, there is nowhere
    to record it."""
    try:
        with sessions.begin() as session:
            record_sync_run(
                session,
                ensure_athlete(session),
                started_at=started_at,
                finished_at=datetime.now(UTC),
                stopped=stopped,
                requests=0,
                counts={},
            )
    except SQLAlchemyError as exc:
        log.error("could not record the failed run: database error %s", exc.__class__.__name__)


def next_delay_s(stopped: str | None, consecutive_failures: int, interval_s: float, catch_up_s: float) -> float:
    """How long to wait before the next scheduled run, given how the last one ended."""
    if stopped == "budget":
        return catch_up_s  # more to do: continue soon
    if stopped == "rate_limited":
        return max(interval_s, RATE_LIMIT_PAUSE_S)  # give Garmin a proper break
    if stopped in {"garmin_error", DATABASE_ERROR, INTERNAL_ERROR}:
        return min(interval_s, catch_up_s * 2 ** min(consecutive_failures, 8))  # back off
    return interval_s  # complete, already running, or auth failed (needs a human anyway)


def run_schedule(
    run_once: Callable[[], str | None],
    wait: Callable[[float], bool],
    *,
    interval_s: float,
    catch_up_s: float,
    max_runs: int | None = None,
) -> int:
    """Run, wait, repeat until `wait` reports a stop request (or `max_runs`). Returns runs made.

    An exception from a run counts as a failed run and backs off. Letting it end the loop would
    make the container restart and sync again at once, repeating Garmin requests every time."""
    runs = failures = 0
    while True:
        try:
            stopped = run_once()
        except Exception as exc:
            log.error("sync run failed: unexpected error %s", exc.__class__.__name__)
            stopped = INTERNAL_ERROR
        runs += 1
        failures = failures + 1 if stopped in FAILURES else 0
        delay = next_delay_s(stopped, failures, interval_s, catch_up_s)
        delay += secrets.randbelow(int(min(300, delay * 0.05)) + 1)  # jitter: no exact rhythm
        log.info("last sync: %s; next in %d minutes", stopped or "complete", round(delay / 60))
        if max_runs is not None and runs >= max_runs:
            return runs
        if wait(delay):
            log.info("stop requested; scheduler exiting")
            return runs


def status(settings: Settings, max_age_hours: float | None = None) -> tuple[dict[str, Any], bool]:
    """Sync health as plain data (dates, counts, stop reasons) and whether it is healthy:
    a run finished within `max_age_hours` that didn't end in a failure."""
    engine = make_engine(settings.database_url.get_secret_value())
    sessions = make_session_factory(engine)
    try:
        with sessions() as session:  # read-only
            athlete = session.scalar(select(Athlete.id).order_by(Athlete.id).limit(1))
            run = last_sync_run(session, athlete) if athlete is not None else None
            cursors = all_cursors(session, athlete) if athlete is not None else {}
            pending_files = count_activities_without_file(session, athlete) if athlete is not None else 0
            pending_fit = count_fit_files_to_parse(session, athlete, PARSER_VERSION) if athlete is not None else 0
    finally:
        engine.dispose()

    now = datetime.now(UTC)
    report: dict[str, Any] = {
        "last_run": None,
        "datasets": {
            name: {"from": r.start.isoformat(), "through": r.end.isoformat()} for name, r in sorted(cursors.items())
        },
        "pending": {"activity_files": pending_files, "fit_files": pending_fit},
    }
    healthy = True
    if run is None:
        healthy = max_age_hours is None
    else:
        age_h = (now - run.finished_at).total_seconds() / 3600
        report["last_run"] = {
            "finished_at": run.finished_at.isoformat(timespec="seconds"),
            "age_hours": round(age_h, 1),
            "complete": run.stopped is None,
            "stopped": run.stopped,
            "requests": run.requests,
        }
        if max_age_hours is not None and (age_h > max_age_hours or run.stopped in FAILURES):
            healthy = False
    report["healthy"] = healthy
    return report, healthy
