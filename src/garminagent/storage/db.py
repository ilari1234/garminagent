"""Database engine and sessions."""

import logging

from sqlalchemy import Engine, create_engine, make_url
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


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
