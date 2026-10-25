"""URL checks for endpoints that receive credentials."""

from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def validate_base_url(url: str) -> str:
    """Return the URL without a trailing slash if it is safe to send credentials to:
    https, or plain http only to this machine (local test servers). Raises ValueError otherwise."""
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("base URL must be an absolute https:// URL")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
        raise ValueError("base URL must use https:// (plain http is allowed only for localhost)")
    if parts.username or parts.password:
        raise ValueError("base URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("base URL must not contain a query or fragment")
    return url.rstrip("/")
