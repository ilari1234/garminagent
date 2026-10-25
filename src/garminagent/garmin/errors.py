"""Errors raised by the Garmin Personal API client.

Messages carry the method, path and status only, never response bodies or credentials.
"""


class GarminError(Exception):
    """Base class for Garmin client errors."""


class GarminAuthError(GarminError):
    """The API key was rejected, or the token response could not be understood."""


class GarminRateLimitError(GarminError):
    """Garmin kept answering 429, or asked us to wait longer than we are willing to.
    Callers should stop the run rather than retry."""


class GarminApiError(GarminError):
    """Garmin answered with an unexpected HTTP status."""

    def __init__(self, status: int, method: str, path: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status}")
        self.status = status
        self.method = method
        self.path = path


class UnexpectedFileError(GarminError):
    """A downloaded activity file is not a zip of FIT files (or a bare FIT file)."""
