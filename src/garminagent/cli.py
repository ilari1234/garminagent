"""Command-line entry point: `garminagent <command>`."""

import json
import logging
import signal
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum

import typer
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from garminagent import __version__
from garminagent.analytics import analyze as run_analyze
from garminagent.analytics.thresholds import (
    THRESHOLD_SPORTS,
    VALUE_TYPES,
    ThresholdOutOfRange,
    ThresholdTimeline,
    check_range,
    load_rows,
    set_manual,
    unset_manual,
)
from garminagent.config import MIN_SYNC_REQUESTS, get_settings
from garminagent.garmin import GarminClient, GarminError
from garminagent.log import configure_logging
from garminagent.renormalize import DATASETS as RENORMALIZE_DATASETS
from garminagent.renormalize import renormalize as run_renormalize
from garminagent.runner import ALREADY_RUNNING, run_schedule, run_sync_once
from garminagent.runner import status as sync_status
from garminagent.storage import migrate
from garminagent.storage.blobs import make_blob_store
from garminagent.storage.db import make_engine, make_session_factory, sync_lock
from garminagent.storage.models import Athlete
from garminagent.storage.repository import ensure_athlete, purge_athlete
from garminagent.sync import FIT_FILES, SyncReport, parse_pending_fit_files

# Tracebacks must never print local variables: the transport holds the Authorization header in
# one. Typer's current default is False; pin it so a default change can't leak secrets.
app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_show_locals=False)
thresholds_app = typer.Typer(
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    help="The threshold timeline: manual values outrank Garmin's zones, which outrank activity values.",
)
app.add_typer(thresholds_app, name="thresholds")
log = logging.getLogger("garminagent")


# Choices as enums, so typer validates them and lists them in --help. Checked against the
# code's own tuples below, so the two can't drift apart.
class RenormalizeDataset(StrEnum):
    activities = "activities"
    daily_summaries = "daily_summaries"
    sleep = "sleep"
    hrv = "hrv"
    weight = "weight"


class ValueTypeChoice(StrEnum):
    ftp = "ftp"
    lthr = "lthr"
    max_hr = "max_hr"
    resting_hr = "resting_hr"
    threshold_speed = "threshold_speed"


class SportChoice(StrEnum):
    all = "all"
    run = "run"
    ride = "ride"
    ski = "ski"
    strength = "strength"
    other = "other"


if (
    tuple(RenormalizeDataset) != tuple(RENORMALIZE_DATASETS)
    or tuple(ValueTypeChoice) != VALUE_TYPES
    or tuple(SportChoice) != THRESHOLD_SPORTS
):
    raise ImportError("CLI choices are out of step with the analytics constants")


@contextmanager
def _athlete_sessions(command: str, *, locked: bool = False) -> Generator[tuple[sessionmaker[Session], int]]:
    """Sessions as the app role and the athlete's id. Any error ends the command with exit code
    1 and the exception class only, never a traceback: messages and frames may quote stored
    data. With `locked`, the sync lock is held throughout, so the command can't interleave with
    a sync writing the same rows; if a sync holds it, the command prints `already_running` and
    exits 0, as `sync` does."""
    engine = make_engine(get_settings().database_url.get_secret_value())
    sessions = make_session_factory(engine)
    try:
        with sync_lock(engine) if locked else _no_lock() as acquired:
            if not acquired:
                log.info("a sync is running; run %s again when it has finished", command)
                typer.echo(json.dumps({"stopped": ALREADY_RUNNING}, indent=2))
                raise typer.Exit(code=0)
            with sessions.begin() as session:
                athlete_id = ensure_athlete(session)
            yield sessions, athlete_id
    except typer.Exit:
        raise  # click's Exit is a RuntimeError; let deliberate exits through
    except SQLAlchemyError as exc:
        log.error("%s failed: database error %s", command, exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        log.error("%s failed: unexpected error %s", command, exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()


@contextmanager
def _no_lock() -> Generator[bool]:
    yield True


@app.callback()
def main() -> None:
    """AI-assisted training planning on Garmin Connect data."""
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def config() -> None:
    """Print the effective settings, with secrets masked."""
    typer.echo(json.dumps(get_settings().redacted(), indent=2))


@app.command("db-check")
def db_check() -> None:
    """Connect to the database and report its server version."""
    engine = make_engine(get_settings().database_url.get_secret_value())
    try:
        with engine.connect() as conn:
            server_version = conn.execute(text("select version()")).scalar_one()
    except SQLAlchemyError as exc:
        log.error("database check failed: %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    log.info("database reachable: %s", server_version)


@app.command("db-upgrade")
def db_upgrade() -> None:
    """Apply database migrations as the schema owner (MIGRATION_DATABASE_URL)."""
    settings = get_settings()
    url = settings.migration_database_url.get_secret_value()
    try:
        migrate.upgrade(url, settings.db_app_role)
        revision = migrate.current_revision(url)
    except SQLAlchemyError as exc:
        log.error("migration failed: %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    log.info("database schema at revision %s", revision)


@app.command("purge-data")
def purge_data(
    yes: bool = typer.Option(False, "--yes", help="Confirm deleting all stored Garmin data."),
) -> None:
    """Permanently delete all stored data: database rows and raw files. The Garmin account
    itself is not touched; data can be synced again later."""
    if not yes:
        log.error("refusing to purge without --yes")
        raise typer.Exit(code=2)
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    sessions = make_session_factory(engine)
    try:
        with sessions() as session:
            athlete_id = session.scalar(select(Athlete.id).order_by(Athlete.id).limit(1))
        if athlete_id is None:
            log.info("nothing to purge")
            return
        result = purge_athlete(sessions, make_blob_store(settings), athlete_id)
    except SQLAlchemyError as exc:
        log.error("purge failed: %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    typer.echo(json.dumps({"rows": result.rows, "blobs": result.blobs}, indent=2))


@app.command("auth-check")
def auth_check() -> None:
    """Check that the Garmin API key works: get a token and read the profile settings.
    Prints only the outcome and the token expiry, never the token or profile data."""
    try:
        with GarminClient.from_settings(get_settings()) as client:
            client.get_user_settings()
            expires_at = client.token_expires_at
    except GarminError as exc:
        log.error("Garmin auth check failed: %s", exc)
        raise typer.Exit(code=1) from exc
    expiry = datetime.fromtimestamp(expires_at, UTC).isoformat(timespec="minutes") if expires_at else "unknown"
    log.info("Garmin API key accepted; access token valid until %s", expiry)


@app.command()
def sync(
    since: datetime | None = typer.Option(  # noqa: B008
        None, "--since", formats=["%Y-%m-%d"], help="Backfill from this date (default: SYNC_START_DATE or 2 years)."
    ),
    max_requests: int | None = typer.Option(
        None, "--max-requests", min=MIN_SYNC_REQUESTS, help="Override SYNC_MAX_REQUESTS (at least 10)."
    ),
) -> None:
    """Sync daily data, activities and activity files from Garmin into the database, once.
    Resumable: re-run until the report says complete. Prints counts only. Skips if another
    sync is running. Exit codes: 0 ok or budget reached, 75 rate limited, 1 error."""
    outcome = run_sync_once(get_settings(), since=since.date() if since else None, max_requests=max_requests)
    if outcome.report is not None:
        report = outcome.report
        summary = {"complete": report.complete, "stopped": report.stopped, "requests": report.requests}
        typer.echo(json.dumps({**summary, "counts": report.counts}, indent=2))
    elif outcome.stopped == ALREADY_RUNNING:
        typer.echo(json.dumps({"complete": False, "stopped": ALREADY_RUNNING}, indent=2))
    if outcome.exit_code:
        raise typer.Exit(code=outcome.exit_code)


@app.command()
def schedule(
    interval_hours: float | None = typer.Option(
        None, "--interval-hours", min=1, help="Override SCHEDULE_INTERVAL_HOURS."
    ),
) -> None:
    """Run sync now and then repeatedly: every SCHEDULE_INTERVAL_HOURS when complete, sooner
    while catching up, later after rate limiting or errors. Stops on SIGTERM/SIGINT (e.g.
    `docker stop`); a run cut off mid-way is safe and continues next time."""
    settings = get_settings()
    stop = threading.Event()

    def request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    interval_s = (interval_hours or settings.schedule_interval_hours) * 3600
    log.info("scheduler started: every %.1f hours", interval_s / 3600)
    run_schedule(
        lambda: run_sync_once(settings).stopped,
        stop.wait,
        interval_s=interval_s,
        catch_up_s=settings.schedule_catch_up_minutes * 60,
    )


@app.command()
def status(
    max_age_hours: float | None = typer.Option(
        None, "--max-age-hours", help="Exit 1 unless a run finished this recently without failing (health check)."
    ),
) -> None:
    """Show sync health: last run, synced range per dataset, pending files. Counts and dates only."""
    try:
        report, healthy = sync_status(get_settings(), max_age_hours)
    except SQLAlchemyError as exc:
        log.error("status failed: database error %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(report, indent=2))
    if not healthy:
        raise typer.Exit(code=1)


@app.command("parse-fit")
def parse_fit_files() -> None:
    """Parse downloaded FIT files into per-second samples and laps. Makes no Garmin requests
    and needs no API key; files parsed by an older parser version are parsed again."""
    settings = get_settings()
    engine = make_engine(settings.database_url.get_secret_value())
    sessions = make_session_factory(engine)
    report = SyncReport()
    try:
        with sessions() as session:
            athlete_id = session.scalar(select(Athlete.id).order_by(Athlete.id).limit(1))
        if athlete_id is not None:
            parse_pending_fit_files(sessions, make_blob_store(settings), athlete_id, report)
    except SQLAlchemyError as exc:
        log.error("parse-fit failed: database error %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    typer.echo(json.dumps(report.counts.get(FIT_FILES, {}), indent=2))


@app.command()
def renormalize(
    dataset: RenormalizeDataset = typer.Argument(..., help="Which normalized table to rebuild."),  # noqa: B008
) -> None:
    """Re-parse stored Garmin responses into a normalized table; the newest stored version of
    each record wins. Makes no Garmin requests and needs no API key. Use after an upgrade that
    reads new fields, e.g. `renormalize activities` for M6a's threshold columns. Prints counts
    only."""
    with _athlete_sessions("renormalize", locked=True) as (sessions, athlete_id):
        counts = run_renormalize(sessions, athlete_id, dataset.value)
    typer.echo(json.dumps(counts, indent=2))


@app.command()
def analyze() -> None:
    """Run the analytics on stored data, as every sync does after FIT parsing: sport groups and
    the threshold timeline (M6a). Makes no Garmin requests. Prints counts, plus Garmin sport
    keys not yet grouped. Exit code 1 if a part failed."""
    with _athlete_sessions("analyze", locked=True) as (sessions, athlete_id):
        result = run_analyze(sessions, athlete_id)
    typer.echo(json.dumps({"counts": result.counts, "unmapped_sport_keys": result.unmapped_keys}, indent=2))
    if result.counts.get("errors"):
        raise typer.Exit(code=1)


@thresholds_app.command("set")
def thresholds_set(
    value_type: ValueTypeChoice = typer.Argument(..., help="Which threshold."),  # noqa: B008
    value: float = typer.Argument(..., help="bpm for HR values, W for ftp, m/s for threshold_speed."),
    sport: SportChoice = typer.Option(SportChoice.all, "--sport", help="Sport group; `all` applies to every group."),  # noqa: B008
    valid_from: datetime | None = typer.Option(  # noqa: B008
        None, "--from", formats=["%Y-%m-%d"], help="First day the value holds (default: today, UTC)."
    ),
) -> None:
    """Set a manual threshold from a date. It outranks Garmin's values from that date until the
    next manual value, or until `thresholds unset` removes it. A value outside the plausible
    range is refused with exit code 2."""
    kind = value_type.value
    day = valid_from.date() if valid_from else datetime.now(UTC).date()
    try:
        check_range(kind, value)
    except ThresholdOutOfRange as exc:
        log.error("refused: %s", exc)  # names the range, never the value
        raise typer.Exit(code=2) from exc
    with _athlete_sessions("thresholds set") as (sessions, athlete_id), sessions.begin() as session:
        set_manual(session, athlete_id, kind, sport.value, day, value)
    typer.echo(json.dumps({"value_type": kind, "sport": sport.value, "valid_from": day.isoformat()}, indent=2))


@thresholds_app.command("unset")
def thresholds_unset(
    value_type: ValueTypeChoice = typer.Argument(..., help="Which threshold."),  # noqa: B008
    valid_from: datetime = typer.Option(..., "--from", formats=["%Y-%m-%d"], help="The manual value's first day."),  # noqa: B008
    sport: SportChoice = typer.Option(SportChoice.all, "--sport", help="Sport group."),  # noqa: B008
) -> None:
    """Remove one manual threshold, so the next source in precedence applies again. Exit code 1
    if there was no such manual value."""
    kind = value_type.value
    with _athlete_sessions("thresholds unset") as (sessions, athlete_id), sessions.begin() as session:
        removed = unset_manual(session, athlete_id, kind, sport.value, valid_from.date())
    typer.echo(json.dumps({"removed": removed}, indent=2))
    if not removed:
        raise typer.Exit(code=1)


@thresholds_app.command("list")
def thresholds_list(
    at: datetime | None = typer.Option(  # noqa: B008
        None, "--at", formats=["%Y-%m-%d"], help="Also show the values in effect on this date."
    ),
    sport: SportChoice = typer.Option(SportChoice.all, "--sport", help="Sport group for --at."),  # noqa: B008
) -> None:
    """Print the threshold timeline and, with --at, the values in effect on a date for a sport
    group. Values go to stdout (your terminal) only, never to logs. Garmin-derived rows are
    refreshed by `analyze`, which every sync runs."""
    with _athlete_sessions("thresholds list") as (sessions, athlete_id), sessions() as session:
        rows = load_rows(session, athlete_id)
    out: dict[str, object] = {
        "timeline": [
            {
                "value_type": r.value_type,
                "sport": r.sport,
                "source": r.source,
                "valid_from": r.valid_from.isoformat(),
                "value": r.value,
            }
            for r in rows
        ]
    }
    if at is not None:
        resolved = ThresholdTimeline(rows).at(at.date(), sport.value)
        out["at"] = {
            "date": at.date().isoformat(),
            "sport": sport.value,
            "values": {
                kind: None
                if r is None
                else {"value": r.value, "source": r.source, "sport": r.sport, "valid_from": r.valid_from.isoformat()}
                for kind, r in resolved.items()
            },
        }
    typer.echo(json.dumps(out, indent=2))
