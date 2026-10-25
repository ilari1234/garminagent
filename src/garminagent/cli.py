"""Command-line entry point: `garminagent <command>`."""

import json
import logging
from datetime import UTC, datetime

import typer
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from garminagent import __version__
from garminagent.config import get_settings
from garminagent.garmin import GarminClient, GarminError
from garminagent.log import configure_logging

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
    engine = create_engine(get_settings().database_url.get_secret_value(), connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            server_version = conn.execute(text("select version()")).scalar_one()
    except SQLAlchemyError as exc:
        log.error("database check failed: %s", exc.__class__.__name__)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()
    log.info("database reachable: %s", server_version)


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
