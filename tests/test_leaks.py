"""The leak detector itself must catch leaks, or the suite-wide check proves nothing."""

import logging

from tests.garmin.fakes import TEST_API_KEY, make_jwt
from tests.leaks import find_leaks


def make_record(msg: str, *args: object, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc else None
    return logging.LogRecord("garminagent.test", logging.INFO, __file__, 1, msg, args, exc_info)


def test_detects_api_key_in_message_args() -> None:
    assert find_leaks([make_record("sending %s", f"ApiKey {TEST_API_KEY}")])


def test_detects_token_in_message() -> None:
    assert find_leaks([make_record(f"Bearer {make_jwt(1_900_000_000)}")])


def test_detects_token_in_exception_traceback() -> None:
    assert find_leaks([make_record("request failed", exc=RuntimeError(make_jwt(None)))])


def test_clean_records_pass() -> None:
    assert find_leaks([make_record("GET %s -> %s", "/userprofile-service/userprofile/settings", 200)]) == []
