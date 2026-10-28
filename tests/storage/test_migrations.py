import pytest
from sqlalchemy import Engine, create_engine, inspect

from garminagent.storage import migrate
from garminagent.storage.models import Base
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration


def test_migrations_match_models(migrated: PgDatabase) -> None:
    migrate.check(migrated.owner_url, migrated.app_role)  # raises on any drift


def test_downgrade_and_upgrade_round_trip(migrated: PgDatabase, owner_engine: Engine) -> None:
    migrate.downgrade(migrated.owner_url, migrated.app_role, "base")
    assert set(inspect(owner_engine).get_table_names()) <= {"alembic_version"}
    migrate.upgrade(migrated.owner_url, migrated.app_role)
    engine = create_engine(migrated.owner_url)
    try:
        assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert migrate.current_revision(migrated.owner_url) == migrate.head_revision()
