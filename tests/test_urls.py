import pytest
from pydantic import SecretStr, ValidationError

from garminagent.config import Settings
from garminagent.garmin import GarminClient
from garminagent.urls import validate_base_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://personal.connectapi.garmin.com", "https://personal.connectapi.garmin.com"),
        ("https://personal.connectapi.garmin.com/", "https://personal.connectapi.garmin.com"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080"),
        ("http://localhost:8080/", "http://localhost:8080"),
        ("http://[::1]:8080", "http://[::1]:8080"),
    ],
)
def test_accepts_https_and_local_http(url: str, expected: str) -> None:
    assert validate_base_url(url) == expected


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://personal.connectapi.garmin.com", "must use https"),
        ("http://localhost.evil.example", "must use https"),
        ("ftp://personal.connectapi.garmin.com", "absolute https"),
        ("personal.connectapi.garmin.com", "absolute https"),
        ("https://user:pw@personal.connectapi.garmin.com", "credentials"),
        ("https://personal.connectapi.garmin.com/?x=1", "query"),
    ],
)
def test_rejects_unsafe_urls(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        validate_base_url(url)


def test_settings_reject_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARMIN_BASE_URL", "http://personal.connectapi.garmin.com")
    with pytest.raises(ValidationError, match="must use https"):
        Settings()


def test_client_rejects_plain_http_even_without_settings() -> None:
    with pytest.raises(ValueError, match="must use https"):
        GarminClient(SecretStr("k"), base_url="http://personal.connectapi.garmin.com")
