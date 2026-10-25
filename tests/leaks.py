"""Leak detection for the test suite: every log record created during a test is checked for
the fake credentials the tests use. A hit means real credentials would leak the same way."""

import logging
import traceback

from tests.garmin.fakes import FAKE_JWT_SIGNATURE, TEST_API_KEY

# Strings that only ever occur inside fake credentials: the API key, and the signature part
# of every fake access token (so a logged token is caught whatever its claims).
CANARIES = (TEST_API_KEY, FAKE_JWT_SIGNATURE)


def record_text(record: logging.LogRecord) -> str:
    text = record.getMessage()
    if record.exc_info and record.exc_info[1] is not None:
        text += "".join(traceback.format_exception(record.exc_info[1]))
    return text


def find_leaks(records: list[logging.LogRecord]) -> list[str]:
    """Descriptions of records that contain a canary; empty if none do."""
    return [
        f"{record.name}:{record.levelname}: contains a fake credential"
        for record in records
        if any(canary in record_text(record) for canary in CANARIES)
    ]
