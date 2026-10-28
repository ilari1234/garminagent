"""The app role can read and write data, and nothing more."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from garminagent.storage import migrate
from garminagent.storage.repository import ensure_athlete

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
