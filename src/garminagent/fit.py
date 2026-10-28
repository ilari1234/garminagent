"""FIT activity files -> per-second samples and laps.

FIT files are untrusted binary input (written by devices and third-party apps), so:
- size and record counts are capped,
- the CRC/integrity check runs before anything is read,
- only known numeric fields are taken; developer fields and free text are ignored,
- values outside physiological / physical ranges become None instead of polluting analysis,
- GPS positions are deliberately not extracted (they stay in the raw file only).

Decoding uses Garmin's official FIT SDK (`garmin-fit-sdk`, FIT Protocol License Agreement; see
SECURITY.md). It is used only here, behind `parse_fit`, so another decoder can replace it.
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from garmin_fit_sdk import Decoder, Stream

# Bump when parsing changes; files parsed by an older version are parsed again.
PARSER_VERSION = 1
MAX_FIT_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 400_000  # ~110 hours at 1 Hz

# (min, max) plausible values; anything outside becomes None.
RANGES = {
    "heart_rate": (20, 250),
    "cadence": (0, 255),
    "power_w": (0, 3000),
    "speed_mps": (0.0, 100.0),
    "distance_m": (0.0, 1_000_000.0),
    "altitude_m": (-500.0, 9000.0),
    "temperature_c": (-50, 70),
    "duration_s": (0.0, 1_000_000.0),
}


@dataclass(frozen=True)
class Sample:
    elapsed_s: int
    heart_rate: int | None = None
    cadence: int | None = None
    power_w: int | None = None
    speed_mps: float | None = None
    distance_m: float | None = None
    altitude_m: float | None = None
    temperature_c: int | None = None


@dataclass(frozen=True)
class Lap:
    lap_index: int
    start_offset_s: int | None = None
    elapsed_s: float | None = None
    timer_s: float | None = None
    distance_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_power_w: int | None = None


@dataclass(frozen=True)
class ParsedFit:
    start_time: datetime
    samples: list[Sample]
    laps: list[Lap]


class FitParseError(Exception):
    """The file can't be used. `reason` is a fixed code that is safe to log."""

    REASONS = ("too_large", "not_fit", "integrity", "decode", "no_records", "too_many_records")

    def __init__(self, reason: str) -> None:
        super().__init__(f"FIT file rejected: {reason}")
        self.reason = reason


def _number(value: Any, field: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    low, high = RANGES[field]
    return number if math.isfinite(number) and low <= number <= high else None


def _int(value: Any, field: str) -> int | None:
    number = _number(value, field)
    return round(number) if number is not None else None


def _first(mesg: dict[str, Any], *names: str) -> Any:
    # Newer devices write enhanced_* (32-bit) fields; older ones the 16-bit originals.
    for name in names:
        if mesg.get(name) is not None:
            return mesg[name]
    return None


def _decode(data: bytes) -> dict[str, list[dict[str, Any]]]:
    if len(data) > MAX_FIT_BYTES:
        raise FitParseError("too_large")
    # Every SDK call is guarded: on hostile input it may raise anything, and an unhandled
    # exception would stop the run before the file is marked, so it would fail every run.
    try:
        if not Decoder(Stream.from_byte_array(bytearray(data))).is_fit():
            raise FitParseError("not_fit")
        if not Decoder(Stream.from_byte_array(bytearray(data))).check_integrity():
            raise FitParseError("integrity")
        messages, errors = Decoder(Stream.from_byte_array(bytearray(data))).read()
    except FitParseError:
        raise
    except Exception as exc:
        raise FitParseError("decode") from exc
    if errors:
        raise FitParseError("decode")
    return cast(dict[str, list[dict[str, Any]]], messages)


def parse_fit(data: bytes) -> ParsedFit:
    messages = _decode(data)
    records = [r for r in messages.get("record_mesgs", []) if isinstance(r.get("timestamp"), datetime)]
    if not records:
        raise FitParseError("no_records")
    if len(records) > MAX_RECORDS:
        raise FitParseError("too_many_records")

    start: datetime = records[0]["timestamp"]
    by_second: dict[int, Sample] = {}
    for record in records:
        elapsed = int((record["timestamp"] - start).total_seconds())
        if elapsed < 0:
            continue  # clock jumps backwards: ignore rather than guess
        # Several records within one second keep the last (FIT "smart recording" varies).
        by_second[elapsed] = Sample(
            elapsed_s=elapsed,
            heart_rate=_int(record.get("heart_rate"), "heart_rate"),
            cadence=_int(record.get("cadence"), "cadence"),
            power_w=_int(record.get("power"), "power_w"),
            speed_mps=_number(_first(record, "enhanced_speed", "speed"), "speed_mps"),
            distance_m=_number(record.get("distance"), "distance_m"),
            altitude_m=_number(_first(record, "enhanced_altitude", "altitude"), "altitude_m"),
            temperature_c=_int(record.get("temperature"), "temperature_c"),
        )

    laps: list[Lap] = []
    for index, lap in enumerate(messages.get("lap_mesgs", [])):
        lap_start = lap.get("start_time")
        offset = int((lap_start - start).total_seconds()) if isinstance(lap_start, datetime) else None
        laps.append(
            Lap(
                lap_index=index,
                start_offset_s=offset,
                elapsed_s=_number(lap.get("total_elapsed_time"), "duration_s"),
                timer_s=_number(lap.get("total_timer_time"), "duration_s"),
                distance_m=_number(lap.get("total_distance"), "distance_m"),
                avg_hr=_int(lap.get("avg_heart_rate"), "heart_rate"),
                max_hr=_int(lap.get("max_heart_rate"), "heart_rate"),
                avg_power_w=_int(lap.get("avg_power"), "power_w"),
            )
        )
    return ParsedFit(start_time=start, samples=[by_second[k] for k in sorted(by_second)], laps=laps)
