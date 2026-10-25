import pytest

from garminagent.garmin import GarminAuthError
from garminagent.garmin.token import DEFAULT_LIFETIME_S, EXPIRY_MARGIN_S, jwt_expiry, make_token, parse_token_response
from tests.garmin.fakes import START_TIME, make_jwt


def test_parses_plain_text_jwt() -> None:
    token = make_jwt(START_TIME)
    assert parse_token_response(f"{token}\n") == token


@pytest.mark.parametrize(
    "body",
    ['{"access_token": "abc.def.ghi", "expires_in": 86400}', '{"accessToken": "abc.def.ghi"}', '"abc.def.ghi"'],
)
def test_parses_json_forms(body: str) -> None:
    assert parse_token_response(body) == "abc.def.ghi"


@pytest.mark.parametrize("body", ["", "   ", "<html>login</html>", '{"expires_in": 86400}', "two words"])
def test_rejects_unrecognized_responses(body: str) -> None:
    with pytest.raises(GarminAuthError):
        parse_token_response(body)


def test_reads_jwt_exp_claim() -> None:
    assert jwt_expiry(make_jwt(START_TIME + 3600)) == START_TIME + 3600


@pytest.mark.parametrize("token", ["not-a-jwt", "a.b", "a.!!!.c", make_jwt(None)])
def test_no_expiry_for_non_jwt_or_missing_claim(token: str) -> None:
    assert jwt_expiry(token) is None


def test_falls_back_to_documented_lifetime() -> None:
    assert make_token("opaque-token", START_TIME).expires_at == START_TIME + DEFAULT_LIFETIME_S


def test_freshness_includes_margin() -> None:
    token = make_token(make_jwt(START_TIME + 3600), START_TIME)
    assert token.is_fresh(START_TIME)
    assert not token.is_fresh(START_TIME + 3600 - EXPIRY_MARGIN_S)


def test_repr_hides_token_value() -> None:
    token = make_token(make_jwt(START_TIME + 3600), START_TIME)
    assert token.value not in repr(token)
