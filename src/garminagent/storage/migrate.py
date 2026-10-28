"""Run Alembic migrations from inside the package (no alembic.ini needed at runtime).

The database URL is handed to Alembic in memory (`Config.attributes`), never written into
an ini file or logged. `app_role` is the least-privilege role the migrations grant data
access to; it must already exist (created by the database's init script or by an operator).
"""

from importlib.resources import files

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from garminagent.storage.db import make_engine


def alembic_config(url: str, app_role: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(files("garminagent.storage") / "migrations"))
    config.attributes["url"] = url
    config.attributes["app_role"] = app_role
    return config


def upgrade(url: str, app_role: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(url, app_role), revision)


def downgrade(url: str, app_role: str, revision: str) -> None:
    command.downgrade(alembic_config(url, app_role), revision)


def check(url: str, app_role: str) -> None:
    """Raise if the models and the migrations disagree."""
    command.check(alembic_config(url, app_role))


def head_revision() -> str | None:
    """The newest migration shipped with this version of the code."""
    return ScriptDirectory.from_config(alembic_config("", "unused")).get_current_head()


def current_revision(url: str) -> str | None:
    engine = make_engine(url)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
