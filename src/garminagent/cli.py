"""Command-line entry point: `garminagent <command>`."""

import json
import logging
import signal
import threading
from datetime import UTC, datetime

import typer
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from garminagent import __version__
from garminagent.config import MIN_SYNC_REQUESTS, get_settings
from garminagent.garmin import GarminClient, GarminError
from garminagent.log import configure_logging
from garminagent.runner import ALREADY_RUNNING, run_schedule, run_sync_once
from garminagent.runner import status as sync_status
from garminagent.storage import migrate
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.db import make_engine, make_session_factory
from garminagent.storage.models import Athlete
from garminagent.storage.repository import purge_athlete
from garminagent.sync import FIT_FILES, SyncReport, parse_pending_fit_files

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("garminagent")


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
        result = purge_athlete(sessions, LocalBlobStore(settings.blob_dir), athlete_id)
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
            parse_pending_fit_files(sessions, LocalBlobStore(settings.blob_dir), athlete_id, report)
    except SQLAlchemyError as exc:
        log.error("parse-fit failed: database error %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    typer.echo(json.dumps(report.counts.get(FIT_FILES, {}), indent=2))
