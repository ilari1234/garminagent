"""Database engine and sessions."""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

# Hosts reached over the machine or the compose network; anything else must use TLS.
LOCAL_DB_HOSTS = {"127.0.0.1", "localhost", "::1", "db"}
TLS_SSLMODES = {"require", "verify-ca", "verify-full"}


def warn_if_insecure(url: str) -> None:
    """Warn when a non-local database is reached without requiring TLS. Every connection path
    (app, migrations) calls this."""
    parsed = make_url(url)
    if parsed.host not in LOCAL_DB_HOSTS and parsed.query.get("sslmode") not in TLS_SSLMODES:
        # Never log the URL itself: it carries the password.
        log.warning("database host %s is not local and sslmode does not require TLS", parsed.host)


def make_engine(url: str) -> Engine:
    warn_if_insecure(url)
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 5})


# Arbitrary fixed key for the Postgres advisory lock that serializes sync runs.
SYNC_LOCK_KEY = 726_473_615


@contextmanager
def sync_lock(engine: Engine) -> Generator[bool]:
    """Hold a session-level advisory lock while syncing; yields False if another run holds it.
    Postgres releases it automatically if the process dies, so a crash can't leave it stuck.
    Advisory locks need no table privileges, so the app role can take them."""
    with engine.connect() as conn:
        acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": SYNC_LOCK_KEY}).scalar())
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_KEY})


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
