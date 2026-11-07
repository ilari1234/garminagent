"""Database engine and sessions."""

import logging
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from garminagent.config import get_settings

log = logging.getLogger(__name__)

# Hosts reached over the machine or the compose network; anything else must use TLS.
LOCAL_DB_HOSTS = {"127.0.0.1", "localhost", "::1", "db"}
TLS_SSLMODES = {"require", "verify-ca", "verify-full"}


class InsecureDatabaseURL(ValueError):
    """DB_REQUIRE_VERIFY_FULL is set and a non-local URL doesn't verify the server's certificate."""


def check_tls(url: str, *, require_verify_full: bool | None = None) -> None:
    """Every connection path (app, migrations, `db-bootstrap`) calls this before connecting.

    A non-local host without `sslmode=verify-full` raises `InsecureDatabaseURL` when
    `require_verify_full` is true (default: DB_REQUIRE_VERIFY_FULL); otherwise a host that
    doesn't even require TLS gets a warning. On AWS the URLs are typed in by hand, so a missing
    `sslmode` would silently fall back to unverified TLS without the hard check. Neither the
    message nor the log line contains the URL: it carries the password.

    A `host` query parameter overrides the URL's host in libpq, so it is the one judged; a
    repeated parameter (a tuple) is never treated as local."""
    parsed = make_url(url)
    query_host = parsed.query.get("host")
    host = query_host if query_host is not None else parsed.host
    if isinstance(host, str) and host in LOCAL_DB_HOSTS:
        return
    sslmode = parsed.query.get("sslmode")
    if require_verify_full is None:
        require_verify_full = get_settings().db_require_verify_full
    if require_verify_full and sslmode != "verify-full":
        raise InsecureDatabaseURL(
            f"DB_REQUIRE_VERIFY_FULL is set: database host {host} needs sslmode=verify-full in its URL"
        )
    if sslmode not in TLS_SSLMODES:
        log.warning("database host %s is not local and sslmode does not require TLS", host)


def make_engine(url: str, *, require_verify_full: bool | None = None) -> Engine:
    """An engine for `url`, after `check_tls` (DB_REQUIRE_VERIFY_FULL unless given). Creating it
    doesn't connect."""
    check_tls(url, require_verify_full=require_verify_full)
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
