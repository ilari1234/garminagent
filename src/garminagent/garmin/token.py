"""Personal API access tokens.

The token endpoint exchanges the API key for an access token. Measured in M0: the response is
`text/plain` containing a bare JWT, with no expiry field, so expiry is read from the JWT's
`exp` claim (no other claims are inspected) and falls back to the documented 24 hours.
"""

import base64
import json
from dataclasses import dataclass, field
from typing import cast

from garminagent.garmin.errors import GarminAuthError

TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
DEFAULT_LIFETIME_S = 24 * 3600
# Renew this long before expiry so a token never expires mid-run.
EXPIRY_MARGIN_S = 300


@dataclass(frozen=True)
class AccessToken:
    value: str = field(repr=False)
    expires_at: float  # Unix time

    def is_fresh(self, now: float) -> bool:
        return now < self.expires_at - EXPIRY_MARGIN_S


def parse_token_response(text: str) -> str:
    """Extract the token from a JSON object, a JSON string or a bare token."""
    text = text.strip()
    try:
        body = cast(object, json.loads(text))
    except ValueError:
        body = None
    if isinstance(body, dict):
        fields = cast(dict[str, object], body)
        for key in ("access_token", "accessToken", "token"):
            value = fields.get(key)
            if isinstance(value, str) and value:
                return value
        raise GarminAuthError("token response is JSON but has no access token field")
    if isinstance(body, str) and body:
        return body
    if text and not text.startswith("<") and not any(c.isspace() for c in text):
        return text
    raise GarminAuthError("token response not recognized")


def jwt_expiry(token: str) -> float | None:
    """The `exp` claim of a JWT, or None if the token isn't a decodable JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = cast(object, json.loads(base64.urlsafe_b64decode(payload)))
    except ValueError:
        return None
    if not isinstance(claims, dict):
        return None
    exp = cast(dict[str, object], claims).get("exp")
    return float(exp) if isinstance(exp, int | float) and not isinstance(exp, bool) else None


def make_token(value: str, now: float) -> AccessToken:
    return AccessToken(value=value, expires_at=jwt_expiry(value) or now + DEFAULT_LIFETIME_S)
