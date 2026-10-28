"""Command-line entry point: `garminagent <command>`."""

import json
import logging
from datetime import UTC, datetime, timedelta

import typer
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from garminagent import __version__
from garminagent.config import get_settings
from garminagent.garmin import GarminAuthError, GarminClient, GarminError
from garminagent.log import configure_logging
from garminagent.storage import migrate
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.db import make_engine, make_session_factory
from garminagent.storage.models import Athlete
from garminagent.storage.repository import purge_athlete
from garminagent.sync import DEFAULT_HISTORY_DAYS, Syncer

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


EXIT_TEMPFAIL = 75  # sysexits.h EX_TEMPFAIL: rate limited, try again later


@app.command()
def sync(
    since: datetime | None = typer.Option(  # noqa: B008
        None, "--since", formats=["%Y-%m-%d"], help="Backfill from this date (default: SYNC_START_DATE or 2 years)."
    ),
    max_requests: int | None = typer.Option(None, "--max-requests", min=1, help="Override SYNC_MAX_REQUESTS."),
) -> None:
    """Sync daily summaries, activities and activity files from Garmin into the database.
    Resumable: re-run until the report says complete. Prints counts only."""
    settings = get_settings()
    today = datetime.now(UTC).date()
    start = since.date() if since else settings.sync_start_date or today - timedelta(days=DEFAULT_HISTORY_DAYS)
    engine = make_engine(settings.database_url.get_secret_value())
    try:
        with GarminClient.from_settings(settings) as client:
            report = Syncer(
                client,
                make_session_factory(engine),
                LocalBlobStore(settings.blob_dir),
                start=start,
                today=today,
                max_requests=max_requests or settings.sync_max_requests,
                refetch_days=settings.sync_refetch_days,
                activity_refetch_days=settings.sync_activity_refetch_days,
            ).run()
    except GarminAuthError as exc:
        log.error("Garmin authentication failed: %s", exc)
        raise typer.Exit(code=1) from exc
    except SQLAlchemyError as exc:
        log.error("sync failed: database error %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    typer.echo(
        json.dumps(
            {
                "complete": report.complete,
                "stopped": report.stopped,
                "requests": report.requests,
                "counts": report.counts,
            },
            indent=2,
        )
    )
    if report.stopped == "rate_limited":
        raise typer.Exit(code=EXIT_TEMPFAIL)
    if report.stopped == "garmin_error":
        raise typer.Exit(code=1)
