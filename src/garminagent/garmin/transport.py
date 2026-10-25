"""HTTP plumbing for the Personal API: tokens, pacing and retries.

Garmin publishes no rate limits and sends no rate-limit headers (M0), so the transport is
conservative: at most one request per `min_interval`, exponential backoff on 429/5xx and
network errors, and a hard stop (GarminRateLimitError) instead of retrying indefinitely.
"""

import logging
import time
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Self

import httpx
from pydantic import SecretStr

from garminagent.garmin.errors import GarminApiError, GarminAuthError, GarminError, GarminRateLimitError
from garminagent.garmin.token import TOKEN_PATH, AccessToken, make_token, parse_token_response
from garminagent.urls import validate_base_url

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"

log = logging.getLogger(__name__)

type QueryParams = Mapping[str, str | int | float | bool | None]


class GarminTransport:
    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = DEFAULT_BASE_URL,
        min_interval: float = 1.0,
        max_retries: int = 4,
        backoff_base: float = 2.0,
        backoff_cap: float = 60.0,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._last_request: float | None = None
        self._token: AccessToken | None = None
        # Checked here as well as in Settings, so no construction path can send credentials
        # over plain http or to a URL with embedded credentials.
        base_url = validate_base_url(base_url)
        self._http = httpx.Client(base_url=base_url, timeout=timeout, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def token_expires_at(self) -> float | None:
        """Unix time at which the current access token expires, if one has been obtained."""
        return self._token.expires_at if self._token else None

    def request(self, method: str, path: str, params: QueryParams | None = None) -> httpx.Response:
        """Send an authenticated request; returns any 2xx response."""
        return self._send(method, path, params, authenticated=True)

    def _ensure_token(self) -> AccessToken:
        if self._token is None or not self._token.is_fresh(self._wall_clock()):
            resp = self._send("POST", TOKEN_PATH, None, authenticated=False)
            self._token = make_token(parse_token_response(resp.text), self._wall_clock())
            log.info("obtained Garmin access token")
        return self._token

    def _send(self, method: str, path: str, params: QueryParams | None, *, authenticated: bool) -> httpx.Response:
        query = {k: _query_value(v) for k, v in (params or {}).items() if v is not None}
        attempt = 0
        renewed_token = False
        while True:
            if authenticated:
                auth = f"Bearer {self._ensure_token().value}"
            else:
                auth = f"ApiKey {self._api_key.get_secret_value()}"
            self._pace()
            started = self._clock()
            try:
                resp = self._http.request(method, path, params=query, headers={"Authorization": auth})
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise GarminError(f"{method} {path}: network error after {attempt + 1} attempts") from exc
                log.warning("%s %s: network error (%s), retrying", method, path, exc.__class__.__name__)
                self._backoff(attempt, None)
                attempt += 1
                continue

            status = resp.status_code
            log.debug("%s %s -> %s in %.0f ms", method, path, status, (self._clock() - started) * 1000)

            if status == 401 and authenticated and not renewed_token:
                # Token revoked or expired early: get a new one once, then retry.
                self._token = None
                renewed_token = True
                continue
            if not authenticated and status in (401, 403):
                raise GarminAuthError(f"API key rejected (HTTP {status}); it may be expired or deleted")
            if status == 429 or status >= 500:
                if attempt >= self._max_retries:
                    if status == 429:
                        raise GarminRateLimitError(f"{method} {path}: still rate limited after {attempt + 1} attempts")
                    raise GarminApiError(status, method, path)
                log.warning("%s %s -> %s, retrying", method, path, status)
                self._backoff(attempt, resp.headers.get("Retry-After"))
                attempt += 1
                continue
            if status >= 400:
                raise GarminApiError(status, method, path)
            return resp

    def _pace(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = min(self._backoff_cap, self._backoff_base * 2**attempt)
        if retry_after is not None:
            try:
                requested = float(retry_after)
            except ValueError:
                requested = None  # HTTP-date form; fall back to our own backoff
            if requested is not None:
                if requested > self._backoff_cap:
                    raise GarminRateLimitError(f"Garmin asked to wait {requested:.0f} s; stopping instead")
                delay = max(delay, requested)
        self._sleep(delay)


def _query_value(value: str | int | float | bool) -> str | int | float:
    # httpx renders bools as "True"/"False"; Garmin's spec expects "true"/"false".
    return ("true" if value else "false") if isinstance(value, bool) else value
