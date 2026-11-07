"""Sport groups from Garmin's activity type keys (ADR 0013).

`activity.sport` holds Garmin's type key unmapped. Analytics group them in code, so a new key
changes no stored data: it lands in `other` and is counted until it's added here.
"""

import re
from typing import Final, Literal

type SportGroup = Literal["run", "ride", "ski", "strength", "other"]

SPORT_GROUPS: Final[dict[str, SportGroup]] = {
    "running": "run",
    "trail_running": "run",
    "cycling": "ride",
    "gravel_cycling": "ride",
    "road_biking": "ride",
    "mountain_biking": "ride",
    "skate_skiing_ws": "ski",
    "cross_country_skiing_ws": "ski",
    "strength_training": "strength",
}
GROUPS: Final[tuple[SportGroup, ...]] = ("run", "ride", "ski", "strength", "other")
# What a type key looks like; anything else is never echoed back, even as a code.
TYPE_KEY: Final = re.compile(r"[a-z0-9_]{1,64}")


def sport_group(type_key: str | None) -> SportGroup:
    """The group for a Garmin type key; `other` for a missing or unmapped key."""
    return SPORT_GROUPS.get(type_key or "", "other")


def is_mapped(type_key: str | None) -> bool:
    return type_key in SPORT_GROUPS
