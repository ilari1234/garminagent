"""Client for the Garmin Connect Personal API (spec: Garmin's OpenAPI document, not in the repo; ADR 0014)."""

from garminagent.garmin.client import RANGE_LIMITS, GarminClient, Json, date_chunks
from garminagent.garmin.errors import (
    GarminApiError,
    GarminAuthError,
    GarminError,
    GarminRateLimitError,
    GarminResponseError,
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
    "GarminResponseError",
    "Json",
    "UnexpectedFileError",
    "date_chunks",
]
