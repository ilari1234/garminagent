import logging

import pytest

from garminagent.storage.db import warn_if_insecure


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@127.0.0.1:5432/x",
        "postgresql+psycopg://u:p@db:5432/x",
        "postgresql+psycopg://u:p@rds.example.test:5432/x?sslmode=verify-full",
    ],
)
def test_no_warning_for_local_or_tls(caplog: pytest.LogCaptureFixture, url: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_insecure(url)
    assert caplog.records == []


@pytest.mark.parametrize(
    "url",
    ["postgresql+psycopg://u:secretpw@rds.example.test:5432/x", "postgresql+psycopg://u:secretpw@h:1/x?sslmode=prefer"],
)
def test_warns_for_remote_without_tls_and_hides_password(caplog: pytest.LogCaptureFixture, url: str) -> None:
    with caplog.at_level(logging.WARNING):
        warn_if_insecure(url)
    assert "does not require TLS" in caplog.text
    assert "secretpw" not in caplog.text
