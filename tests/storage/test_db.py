import logging
import secrets

import pytest
from sqlalchemy import URL

from garminagent.storage.db import warn_if_insecure

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
        warn_if_insecure(url)
    assert caplog.records == []


@pytest.mark.parametrize("url", [pg_url("rds.example.test"), pg_url("rds.example.test", sslmode="prefer")])
def test_warns_for_remote_without_tls_and_hides_password(caplog: pytest.LogCaptureFixture, url: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_insecure(url)
    assert "does not require TLS" in caplog.text
    assert PASSWORD not in caplog.text
