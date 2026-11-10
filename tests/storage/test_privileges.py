"""The app role can read and write data, and nothing more."""

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from garminagent.storage import migrate
from garminagent.storage.repository import ensure_athlete
from tests.storage.conftest import PgDatabase

pytestmark = pytest.mark.integration


def test_app_role_can_read_and_write_data(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete_id = ensure_athlete(session)
    with app_sessions() as session:
        assert session.execute(text("SELECT id FROM athlete")).scalar_one() == athlete_id
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == migrate.head_revision()


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE intruder (id int)",
        "DROP TABLE athlete",
        "ALTER TABLE athlete ADD COLUMN extra int",
        "TRUNCATE athlete",
        "DELETE FROM alembic_version",
        "UPDATE alembic_version SET version_num = 'x'",
        "CREATE ROLE intruder",
    ],
)
def test_app_role_cannot_change_schema_or_migrations(app_sessions: sessionmaker[Session], statement: str) -> None:
    with app_sessions() as session, pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        session.execute(text(statement))


NEW_IN_0006 = ("athlete_zones", "athlete_threshold")


def test_app_role_can_write_the_m6a_tables(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete_id = ensure_athlete(session)
        session.execute(
            text("INSERT INTO athlete_zones (athlete_id, sport, valid_from) VALUES (:a, 'DEFAULT', '2026-03-01')"),
            {"a": athlete_id},
        )
        session.execute(
            text(
                "INSERT INTO athlete_threshold (athlete_id, value_type, sport, source, valid_from, value)"
                " VALUES (:a, 'ftp', 'ride', 'manual', '2026-03-01', 250)"
            ),
            {"a": athlete_id},
        )
        session.execute(text("UPDATE athlete_threshold SET value = 255"))
        session.execute(text("DELETE FROM athlete_zones"))


@pytest.mark.parametrize("table", NEW_IN_0006)
def test_app_role_cannot_change_the_m6a_tables(app_sessions: sessionmaker[Session], table: str) -> None:
    for statement in (f"TRUNCATE {table}", f"ALTER TABLE {table} ADD COLUMN extra int", f"DROP TABLE {table}"):
        with app_sessions() as session, pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
            session.execute(text(statement))


NEW_IN_0007 = ("activity_metrics",)
METRICS_ROW = (
    "INSERT INTO activity_metrics (athlete_id, activity_id, activity_date, sport_group, algo_version,"
    " input_fingerprint, load_method, thresholds_backfilled)"
    " VALUES (:a, 1, '2026-03-01', 'run', 1, repeat('0', 64), :method, false)"
)


def _activity(session: Session) -> int:
    athlete_id = ensure_athlete(session)
    session.execute(
        text("INSERT INTO activity (athlete_id, activity_id, start_time_utc) VALUES (:a, 1, '2026-03-01T05:00:00Z')"),
        {"a": athlete_id},
    )
    return athlete_id


def test_app_role_can_write_the_m6b_table(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete_id = _activity(session)
        session.execute(text(METRICS_ROW), {"a": athlete_id, "method": "none"})
        session.execute(text("UPDATE activity_metrics SET load_method = 'hr', load = 50"))
        session.execute(text("DELETE FROM activity_metrics"))


@pytest.mark.parametrize("table", NEW_IN_0007)
def test_app_role_cannot_change_the_m6b_table(app_sessions: sessionmaker[Session], table: str) -> None:
    for statement in (f"TRUNCATE {table}", f"ALTER TABLE {table} ADD COLUMN extra int", f"DROP TABLE {table}"):
        with app_sessions() as session, pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
            session.execute(text(statement))


def test_activity_metrics_refuses_free_text_in_code_columns(app_sessions: sessionmaker[Session]) -> None:
    with app_sessions.begin() as session:
        athlete_id = _activity(session)
    with app_sessions() as session, pytest.raises(IntegrityError, match="ck_activity_metrics_load_method"):
        session.execute(text(METRICS_ROW), {"a": athlete_id, "method": "freetext"})


@pytest.mark.parametrize(
    ("revision", "tables", "sequences"), [("0005", NEW_IN_0006, True), ("0006", NEW_IN_0007, False)]
)
def test_new_tables_are_granted_explicitly_without_default_privileges(
    migrated: PgDatabase, owner_engine: Engine, revision: str, tables: tuple[str, ...], sequences: bool
) -> None:
    """ADR 0013: new tables are granted by their own migration (0006 on), not by 0001's default
    privileges, which only cover tables created by the role that set them."""
    role = migrated.app_role
    migrate.downgrade(migrated.owner_url, role, revision)
    with owner_engine.begin() as conn:
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {role}"))
        conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM {role}"))
    try:
        migrate.upgrade(migrated.owner_url, role)
        with owner_engine.connect() as conn:
            for table in tables:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert conn.execute(
                        text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": table, "p": privilege}
                    ).scalar_one(), f"{role} lacks {privilege} on {table}"
                for privilege in ("TRUNCATE", "REFERENCES", "TRIGGER"):
                    assert not conn.execute(
                        text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": table, "p": privilege}
                    ).scalar_one(), f"{role} has {privilege} on {table}"
                if sequences:
                    assert conn.execute(
                        text("SELECT has_sequence_privilege(:r, :s, 'USAGE')"), {"r": role, "s": f"{table}_id_seq"}
                    ).scalar_one()
    finally:
        with owner_engine.begin() as conn:  # restore what 0001 set, for the other tests
            data = "SELECT, INSERT, UPDATE, DELETE"
            conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT {data} ON TABLES TO {role}"))
            conn.execute(text(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}"))
