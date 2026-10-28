import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from garminagent.config import Settings, get_settings
from tests.garmin.fakes import FakeGarmin, FakeTime
from tests.leaks import find_leaks


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run every test in an empty directory with no app env vars, so a developer's
    real .env (and API key) is never read."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def no_credentials_in_logs() -> Iterator[None]:
    """Fail any test whose log records contain a fake credential. Hooks record creation
    rather than a handler, so it still sees everything when code reconfigures logging."""
    records: list[logging.LogRecord] = []
    previous = logging.getLogRecordFactory()

    def capture(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        records.append(record)
        return record

    # Records below the logger's level are never created, so check at DEBUG: a leak at a
    # level that is off in tests would still leak in a LOG_LEVEL=DEBUG run.
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    logging.setLogRecordFactory(capture)
    try:
        yield
    finally:
        logging.setLogRecordFactory(previous)
        root.setLevel(previous_level)
    leaks = find_leaks(records)
    assert not leaks, f"credential leaked into logs: {leaks}"


@pytest.fixture
def clock() -> FakeTime:
    return FakeTime()


@pytest.fixture
def garmin(clock: FakeTime) -> FakeGarmin:
    """A fake Garmin Personal API (see tests/garmin/fakes.py)."""
    return FakeGarmin(clock)
