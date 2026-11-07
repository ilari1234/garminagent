import logging
import secrets

import pytest
from sqlalchemy import URL

from garminagent.config import get_settings
from garminagent.storage import migrate
from garminagent.storage.db import InsecureDatabaseURL, check_tls, make_engine

# Built at runtime so the repo holds no credential-shaped literals (secret scanners flag them).
PASSWORD = secrets.token_hex(8)


def pg_url(host: str, **query: str) -> str:
    return URL.create(
        "postgresql+psycopg", username="u", password=PASSWORD, host=host, port=5432, database="x", query=query
    ).render_as_string(hide_password=False)


@pytest.mark.parametrize(
    "url",
    [pg_url("127.0.0.1"), pg_url("db"), pg_url("rds.example.test", sslmode="verify-full")],
)
def test_no_warning_for_local_or_tls(caplog: pytest.LogCaptureFixture, url: str) -> None:
    with caplog.at_level(logging.WARNING):
        check_tls(url)
    assert caplog.records == []


@pytest.mark.parametrize("url", [pg_url("rds.example.test"), pg_url("rds.example.test", sslmode="prefer")])
def test_warns_for_remote_without_tls_and_hides_password(caplog: pytest.LogCaptureFixture, url: str) -> None:
    with caplog.at_level(logging.WARNING):
        check_tls(url)
    assert "does not require TLS" in caplog.text
    assert PASSWORD not in caplog.text


@pytest.mark.parametrize(
    "url",
    [
        pg_url("rds.example.test"),
        pg_url("rds.example.test", sslmode="require"),
        pg_url("rds.example.test", sslmode="verify-ca"),
    ],
)
def test_require_verify_full_refuses_anything_less_for_remote_hosts(url: str) -> None:
    with pytest.raises(InsecureDatabaseURL, match="sslmode=verify-full") as info:
        check_tls(url, require_verify_full=True)
    assert PASSWORD not in str(info.value)


@pytest.mark.parametrize("url", [pg_url("127.0.0.1"), pg_url("db"), pg_url("rds.example.test", sslmode="verify-full")])
def test_require_verify_full_allows_local_hosts_and_verify_full(url: str) -> None:
    check_tls(url, require_verify_full=True)


def test_a_host_query_parameter_is_judged_not_the_url_host() -> None:
    """libpq connects to `?host=`, so a local URL host must not hide a remote one."""

    def with_query_host(url_host: str, query_host: str) -> str:
        query = {"host": query_host}
        return URL.create(
            "postgresql+psycopg", username="u", password=PASSWORD, host=url_host, database="x", query=query
        ).render_as_string(hide_password=False)

    with pytest.raises(InsecureDatabaseURL, match=r"rds\.example\.test"):
        check_tls(with_query_host("127.0.0.1", "rds.example.test"), require_verify_full=True)
    check_tls(with_query_host("rds.example.test", "db"), require_verify_full=True)


def test_require_verify_full_comes_from_the_setting_on_every_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """make_engine is how the app, `db-bootstrap` and `current_revision` connect; migrations go
    through the Alembic env. Both refuse before any connection is attempted."""
    insecure = pg_url("rds.example.test", sslmode="require")
    make_engine(insecure).dispose()  # off by default: a warning only
    monkeypatch.setenv("DB_REQUIRE_VERIFY_FULL", "true")
    get_settings.cache_clear()
    with pytest.raises(InsecureDatabaseURL):
        make_engine(insecure)
    with pytest.raises(InsecureDatabaseURL):
        migrate.upgrade(insecure, "garminagent_app")
    with pytest.raises(InsecureDatabaseURL):
        migrate.current_revision(insecure)
