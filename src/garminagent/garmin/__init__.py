"""Client for the Garmin Connect Personal API (spec: docs/garmin-personal-api/openapi.json)."""

from garminagent.garmin.client import RANGE_LIMITS, GarminClient, Json, date_chunks
from garminagent.garmin.errors import (
    GarminApiError,
    GarminAuthError,
    GarminError,
    GarminRateLimitError,
    UnexpectedFileError,
)
from garminagent.garmin.files import ActivityFile

__all__ = [
    "RANGE_LIMITS",
    "ActivityFile",
    "GarminApiError",
    "GarminAuthError",
    "GarminClient",
    "GarminError",
    "GarminRateLimitError",
    "Json",
    "UnexpectedFileError",
    "date_chunks",
]
