"""Command-line entry point: `garminagent <command>`."""

import json
import logging

import typer
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from garminagent import __version__
from garminagent.config import get_settings
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
