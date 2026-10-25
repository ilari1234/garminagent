"""Postgres fixtures. TEST_DATABASE_URL points at a server where the user may create databases
and roles; each test session works in its own throwaway database and app role, so a
developer's real data is never touched."""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from garminagent.storage import migrate
from garminagent.storage.blobs import LocalBlobStore
from garminagent.storage.db import make_engine, make_session_factory
from garminagent.storage.models import Base


@dataclass(frozen=True)
class PgDatabase:
    owner_url: str
    app_url: str
    app_role: str


@pytest.fixture(scope="session")
def test_database() -> Iterator[PgDatabase]:
    server_url = os.environ.get("TEST_DATABASE_URL")
    if not server_url:
        pytest.skip("TEST_DATABASE_URL not set")
    suffix = secrets.token_hex(4)
    db_name, role, password = f"garminagent_test_{suffix}", f"ga_app_test_{suffix}", secrets.token_hex(16)
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    server = make_url(server_url)
    # Names and password are generated hex above; DDL can't take bind parameters.
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOCREATEDB NOCREATEROLE"))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO {role}'))
    try:
        yield PgDatabase(
            owner_url=server.set(database=db_name).render_as_string(hide_password=False),
            app_url=server.set(database=db_name, username=role, password=password).render_as_string(
                hide_password=False
            ),
            app_role=role,
        )
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        admin.dispose()


@pytest.fixture(scope="session")
def migrated(test_database: PgDatabase) -> PgDatabase:
    migrate.upgrade(test_database.owner_url, test_database.app_role)
    return test_database


@pytest.fixture
def owner_engine(migrated: PgDatabase) -> Iterator[Engine]:
    engine = create_engine(migrated.owner_url)
    yield engine
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture
def app_sessions(migrated: PgDatabase, owner_engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Sessions as the least-privilege app role, like the real app."""
    engine = make_engine(migrated.app_url)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def blobs(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")
