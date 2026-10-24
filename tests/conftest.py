from collections.abc import Iterator
from pathlib import Path

import pytest

from garminagent.config import Settings, get_settings


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
