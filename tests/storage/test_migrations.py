import pytest
from sqlalchemy import Engine, create_engine, inspect, text

from garminagent.storage import migrate
from garminagent.storage.models import Base
from tests.storage.conftest import PgDatabase
from tests.storage.test_schema import FORBIDDEN

pytestmark = pytest.mark.integration


def test_migrations_match_models(migrated: PgDatabase) -> None:
    migrate.check(migrated.owner_url, migrated.app_role)  # raises on any drift


DERIVED_VIEWS = ("daily_load_weekly",)


def test_every_view_belongs_to_a_layer_and_has_no_location_or_free_text_columns(
    migrated: PgDatabase, owner_engine: Engine
) -> None:
    """The schema guard in test_schema.py sees only mapped tables; views live in migrations alone,
    so this checks them in the database."""
    with owner_engine.connect() as conn:
        views = (
            conn.execute(text("SELECT table_name FROM information_schema.views WHERE table_schema = 'public'"))
            .scalars()
            .all()
        )
        assert sorted(views) == sorted(DERIVED_VIEWS)
        for view in views:
            columns = (
                conn.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name = :v"), {"v": view}
                )
                .scalars()
                .all()
            )
            offending = [c for c in columns if any(word in c for word in FORBIDDEN)]
            assert columns and not offending, f"{view} has columns {offending}"


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
