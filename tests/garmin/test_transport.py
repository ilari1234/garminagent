import httpx
import pytest

from garminagent.garmin import GarminApiError, GarminAuthError, GarminError, GarminRateLimitError
from garminagent.garmin.token import EXPIRY_MARGIN_S, TOKEN_PATH
from tests.garmin.fakes import TEST_API_KEY, FakeGarmin, FakeTime, make_jwt

SETTINGS = "/userprofile-service/userprofile/settings"


def test_exchanges_key_for_token_and_reuses_it(garmin: FakeGarmin) -> None:
    garmin.json(SETTINGS, {"ok": True})
    with garmin.client() as client:
        for _ in range(3):
            assert client.get_user_settings() == {"ok": True}

    token_calls = garmin.requests_to(TOKEN_PATH)
    assert len(token_calls) == 1
    assert token_calls[0].authorization == f"ApiKey {TEST_API_KEY}"
    assert all(s.authorization.startswith("Bearer ") for s in garmin.requests_to(SETTINGS))


def test_renews_token_before_it_expires(garmin: FakeGarmin, clock: FakeTime) -> None:
    garmin.json(SETTINGS, {})
    with garmin.client() as client:
        client.get_user_settings()
        clock.advance(24 * 3600 - EXPIRY_MARGIN_S)
        client.get_user_settings()
    assert len(garmin.requests_to(TOKEN_PATH)) == 2


def test_renews_token_once_on_401(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(401), httpx.Response(200, json={}))
    with garmin.client() as client:
        assert client.get_user_settings() == {}
    assert len(garmin.requests_to(TOKEN_PATH)) == 2


def test_gives_up_after_second_401(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(401))
    with garmin.client() as client, pytest.raises(GarminApiError) as excinfo:
        client.get_user_settings()
    assert excinfo.value.status == 401


def test_rejected_key_raises_auth_error_without_leaking_it(garmin: FakeGarmin) -> None:
    garmin.reply("POST", TOKEN_PATH, httpx.Response(401, text="nope"))
    with garmin.client() as client, pytest.raises(GarminAuthError) as excinfo:
        client.get_user_settings()
    assert TEST_API_KEY not in str(excinfo.value)


def test_honors_retry_after_on_429(garmin: FakeGarmin, clock: FakeTime) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={}))
    with garmin.client() as client:
        assert client.get_user_settings() == {}
    assert 7 in clock.sleeps


def test_stops_when_asked_to_wait_too_long(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(429, headers={"Retry-After": "3600"}))
    with garmin.client() as client, pytest.raises(GarminRateLimitError):
        client.get_user_settings()
    assert len(garmin.requests_to(SETTINGS)) == 1


def test_stops_after_repeated_429(garmin: FakeGarmin, clock: FakeTime) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(429))
    with garmin.client(max_retries=3) as client, pytest.raises(GarminRateLimitError):
        client.get_user_settings()
    assert len(garmin.requests_to(SETTINGS)) == 4
    assert [s for s in clock.sleeps if s >= 2] == [2, 4, 8]


def test_retries_server_errors(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(503), httpx.Response(502), httpx.Response(200, json={}))
    with garmin.client() as client:
        assert client.get_user_settings() == {}


def test_retries_network_errors(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.ConnectError("boom"), httpx.Response(200, json={}))
    with garmin.client() as client:
        assert client.get_user_settings() == {}


def test_network_errors_eventually_raise(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.ConnectError("boom"))
    with garmin.client(max_retries=1) as client, pytest.raises(GarminError):
        client.get_user_settings()


def test_client_errors_are_not_retried(garmin: FakeGarmin) -> None:
    garmin.reply("GET", SETTINGS, httpx.Response(400, json={}))
    with garmin.client() as client, pytest.raises(GarminApiError) as excinfo:
        client.get_user_settings()
    assert excinfo.value.status == 400
    assert len(garmin.requests_to(SETTINGS)) == 1


def test_paces_back_to_back_requests(garmin: FakeGarmin, clock: FakeTime) -> None:
    garmin.json(SETTINGS, {})
    with garmin.client(min_interval=1.5) as client:
        client.get_user_settings()  # token request + this request
        client.get_user_settings()
    assert clock.sleeps == [1.5, 1.5]


def test_no_pacing_delay_when_requests_are_far_apart(garmin: FakeGarmin, clock: FakeTime) -> None:
    garmin.json(SETTINGS, {})
    with garmin.client(min_interval=1.0) as client:
        client.get_user_settings()  # paced once: right after the token request
        clock.advance(5)
        client.get_user_settings()
    assert clock.sleeps == [1.0]


def test_query_params_render_bools_and_skip_none(garmin: FakeGarmin) -> None:
    garmin.json("/biometric-service/heartRateZones/v2", [])
    with garmin.client() as client:
        client.get_heart_rate_zones(generate_defaults=True)
        client.get_heart_rate_zones()
    first, second = garmin.requests_to("/biometric-service/heartRateZones/v2")
    assert first.params == {"generateDefaults": "true"}
    assert second.params == {}


def test_token_expiry_is_exposed(garmin: FakeGarmin, clock: FakeTime) -> None:
    expires_at = clock.time() + 600
    garmin.reply("POST", TOKEN_PATH, httpx.Response(200, text=make_jwt(expires_at)))
    garmin.json(SETTINGS, {})
    with garmin.client() as client:
        assert client.token_expires_at is None
        client.get_user_settings()
        assert client.token_expires_at == expires_at
