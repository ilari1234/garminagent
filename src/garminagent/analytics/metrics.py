"""Per-activity metrics from FIT samples (ADR 0013, M6b). Pure functions: no database, no I/O.

**One gap rule for everything.** `activity_sample.elapsed_s` has gaps (smart recording,
auto-pause) and no pause flag. A sample covers the seconds until the next one when that gap is
at most MAX_GAP_S, with its values carried forward to 1 s resolution. A longer gap is a pause:
the sample before it covers its own second only, the gap counts toward no total, and rolling
windows don't cross it. The last sample also covers one second, so 3600 samples at 1 Hz are an
hour. "Moving time" here means time not paused by this rule; a stop the watch kept recording
through still counts.

**Names are neutral** (ADR 0013): weighted power, intensity and power load stand for
trademarked metrics with the same published arithmetic; HR load is our own scaled Banister
TRIMP. Change any formula or constant here and bump ALGO_VERSION, so every row is recomputed.
"""

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from garminagent.analytics.sports import SportGroup

ALGO_VERSION: Final = 1
MAX_GAP_S: Final = 10
WP_WINDOW_S: Final = 30
# A load, efficiency or decoupling from a channel needs it on at least this share of moving
# time. Missing seconds are never filled in: a partial session gives no value, not a guess.
MIN_COVERAGE: Final = 0.5
DECOUPLING_MIN_MOVING_S: Final = 3600
DECOUPLING_SKIP_S: Final = 600
# Coggan's levels 1-6 end at these fractions of FTP (inclusive); level 7 is above the last.
POWER_LEVEL_UPPER: Final = (0.55, 0.75, 0.90, 1.05, 1.20, 1.50)
# Groups whose power never makes the load: running load is HR-only in phase 2 (ADR 0013).
NO_POWER_LOAD_GROUPS: Final[frozenset[SportGroup]] = frozenset({"run"})

type LoadMethod = Literal["power", "hr", "none"]
type EfBasis = Literal["power", "speed"]


@dataclass(frozen=True)
class Point:
    elapsed_s: int
    heart_rate: int | None
    power_w: int | None
    speed_mps: float | None


@dataclass(frozen=True)
class Second:
    heart_rate: int | None
    power_w: int | None
    speed_mps: float | None


type Segment = list[Second]


def expand(points: Iterable[Point]) -> list[Segment]:
    """Samples -> runs of consecutive 1 s slots, split at pauses (see the module docstring)."""
    ordered = sorted(points, key=lambda p: p.elapsed_s)
    segments: list[Segment] = []
    current: Segment = []
    for i, point in enumerate(ordered):
        gap = ordered[i + 1].elapsed_s - point.elapsed_s if i + 1 < len(ordered) else None
        paused = gap is None or gap > MAX_GAP_S
        cover = 1 if paused or gap is None else gap
        current.extend([Second(point.heart_rate, point.power_w, point.speed_mps)] * cover)
        if paused:
            segments.append(current)
            current = []
    return segments


def weighted_power(segments: Sequence[Segment]) -> float | None:
    """30 s rolling mean from the 30th second, raised to the 4th power, averaged, 4th root.
    Windows cross neither pauses nor seconds without power."""
    total = 0.0
    windows = 0
    for run in _runs(segments, lambda s: s.power_w):
        if len(run) < WP_WINDOW_S:
            continue
        rolling = float(sum(run[:WP_WINDOW_S]))
        for i in range(WP_WINDOW_S, len(run) + 1):
            if i > WP_WINDOW_S:
                rolling += run[i - 1] - run[i - 1 - WP_WINDOW_S]
            total += (rolling / WP_WINDOW_S) ** 4
            windows += 1
    return (total / windows) ** 0.25 if windows else None


def _runs(segments: Sequence[Segment], value: Callable[[Second], float | None]) -> list[list[float]]:
    """Maximal runs of seconds that have the value, within segments."""
    runs: list[list[float]] = []
    for segment in segments:
        run: list[float] = []
        for second in segment:
            v = value(second)
            if v is None:
                if run:
                    runs.append(run)
                run = []
            else:
                run.append(float(v))
        if run:
            runs.append(run)
    return runs


def power_load(moving_s: int, wp: float, ftp: float) -> float:
    """`s * WP * I / (FTP * 3600) * 100` with `I = WP / FTP`: an hour at FTP is 100."""
    return moving_s * wp * (wp / ftp) / (ftp * 3600) * 100


def hr_load(heart_rates: Iterable[int], resting: float, max_hr: float, lthr: float, b: float) -> float | None:
    """Banister TRIMP per second, `x·e^(b·x)` with x the HR reserve fraction, scaled so an hour
    at LTHR is 100 (the 0.64 factor and time units cancel). x is floored at 0, so HR below
    resting adds nothing; it isn't capped at 1, so HR above a stale max HR still counts.
    None when the thresholds can't define a reserve or a positive LTHR reference."""
    reserve = max_hr - resting
    if reserve <= 0 or lthr <= resting:
        return None
    x_lt = (lthr - resting) / reserve
    reference = 3600 * x_lt * math.exp(b * x_lt)
    total = 0.0
    for hr in heart_rates:
        x = max(0.0, (hr - resting) / reserve)
        total += x * math.exp(b * x)
    return 100 * total / reference


def hr_zone_seconds(heart_rates: Iterable[int], floors: Sequence[int]) -> list[int]:
    """Seconds in zone 0 (below zone 1's floor) to zone 5: zone k is [floor k, floor k+1)."""
    counts = [0] * 6
    for hr in heart_rates:
        zone = 0
        for k, floor in enumerate(floors, start=1):
            if hr >= floor:
                zone = k
        counts[zone] += 1
    return counts


def power_level_seconds(powers: Iterable[float], ftp: float) -> list[int]:
    """Seconds in Coggan's levels 1-7, by each second's power as a fraction of FTP."""
    counts = [0] * 7
    for power in powers:
        fraction = power / ftp
        level = next((k for k, upper in enumerate(POWER_LEVEL_UPPER) if fraction <= upper), 6)
        counts[level] += 1
    return counts


def three_zone_split(zones: Sequence[int]) -> tuple[int, int, int]:
    """Garmin's five HR zones folded into three: below zone 3 (and below zone 1) is low, zone 3
    mid, zones 4-5 high. A convention, as close to the two thresholds as HR zones allow."""
    return zones[0] + zones[1] + zones[2], zones[3], zones[4] + zones[5]


def efficiency(seconds: Sequence[Second], basis: EfBasis) -> float | None:
    """Mean output (W, or m/s) over mean HR, over the seconds that have both; None when fewer
    than MIN_COVERAGE of the seconds do."""
    outputs: list[float] = []
    heart_rates: list[int] = []
    for s in seconds:
        output = s.power_w if basis == "power" else s.speed_mps
        if output is not None and s.heart_rate is not None:
            outputs.append(float(output))
            heart_rates.append(s.heart_rate)
    if not seconds or len(outputs) < MIN_COVERAGE * len(seconds):
        return None
    return (sum(outputs) / len(outputs)) / (sum(heart_rates) / len(heart_rates))


def decoupling(seconds: Sequence[Second], basis: EfBasis) -> float | None:
    """`1 - EF2 / EF1` over the two halves of moving time after the first 10 minutes, for
    sessions of at least an hour. Positive: HR drifted up against output."""
    if len(seconds) < DECOUPLING_MIN_MOVING_S:
        return None
    rest = seconds[DECOUPLING_SKIP_S:]
    half = len(rest) // 2
    ef1, ef2 = efficiency(rest[:half], basis), efficiency(rest[half : 2 * half], basis)
    if ef1 is None or ef2 is None or ef1 == 0:
        return None
    return 1 - ef2 / ef1


@dataclass(frozen=True)
class Thresholds:
    """The values a session is measured against; None where not resolved."""

    ftp_w: float | None
    lthr: float | None
    max_hr: float | None
    resting_hr: float | None
    zone_floors: Sequence[int] | None


@dataclass(frozen=True)
class Metrics:
    moving_s: int | None
    hr_coverage: float | None
    power_coverage: float | None
    speed_coverage: float | None
    weighted_power_w: float | None
    intensity: float | None
    power_load: float | None
    hr_load: float | None
    load: float | None
    load_method: LoadMethod
    hr_zone_s: list[int] | None  # zone 0 (below zone 1) to zone 5
    power_level_s: list[int] | None  # levels 1-7
    three_zone_s: tuple[int, int, int] | None  # low, mid, high
    efficiency_factor: float | None
    ef_basis: EfBasis | None
    decoupling: float | None
    hr_load_not_configured: bool  # HR load would have been the load, but ANALYTICS_TRIMP_B is unset


NO_SAMPLES: Final = Metrics(
    moving_s=None,
    hr_coverage=None,
    power_coverage=None,
    speed_coverage=None,
    weighted_power_w=None,
    intensity=None,
    power_load=None,
    hr_load=None,
    load=None,
    load_method="none",
    hr_zone_s=None,
    power_level_s=None,
    three_zone_s=None,
    efficiency_factor=None,
    ef_basis=None,
    decoupling=None,
    hr_load_not_configured=False,
)


def compute(points: Iterable[Point], group: SportGroup, thresholds: Thresholds, trimp_b: float | None) -> Metrics:
    segments = expand(points)
    seconds = [s for segment in segments for s in segment]
    moving = len(seconds)
    if not moving:
        return NO_SAMPLES
    heart_rates = [s.heart_rate for s in seconds if s.heart_rate is not None]
    powers = [float(s.power_w) for s in seconds if s.power_w is not None]
    hr_cov, power_cov = len(heart_rates) / moving, len(powers) / moving
    speed_cov = sum(1 for s in seconds if s.speed_mps is not None) / moving
    hr_ok, power_ok = hr_cov >= MIN_COVERAGE, power_cov >= MIN_COVERAGE

    wp = weighted_power(segments) if power_ok else None
    ftp = thresholds.ftp_w
    power_counts = group not in NO_POWER_LOAD_GROUPS and wp is not None and ftp is not None
    intensity = wp / ftp if power_counts and wp is not None and ftp is not None else None
    p_load = power_load(moving, wp, ftp) if power_counts and wp is not None and ftp is not None else None
    levels = power_level_seconds(powers, ftp) if power_counts and ftp is not None else None

    h_load = None
    hr_thresholds = (thresholds.resting_hr, thresholds.max_hr, thresholds.lthr)
    if hr_ok and trimp_b is not None and None not in hr_thresholds:
        resting, max_hr, lthr = (float(v) for v in hr_thresholds if v is not None)
        h_load = hr_load(heart_rates, resting, max_hr, lthr, trimp_b)

    load, method = (p_load, "power") if p_load is not None else (h_load, "hr") if h_load is not None else (None, "none")
    zones = hr_zone_seconds(heart_rates, thresholds.zone_floors) if thresholds.zone_floors and heart_rates else None
    basis: EfBasis | None = "power" if power_ok else "speed" if speed_cov >= MIN_COVERAGE else None
    ef = efficiency(seconds, basis) if basis and hr_ok else None
    return Metrics(
        moving_s=moving,
        hr_coverage=hr_cov,
        power_coverage=power_cov,
        speed_coverage=speed_cov,
        weighted_power_w=wp,
        intensity=intensity,
        power_load=p_load,
        hr_load=h_load,
        load=load,
        load_method=method,
        hr_zone_s=zones,
        power_level_s=levels,
        three_zone_s=three_zone_split(zones) if zones else None,
        efficiency_factor=ef,
        ef_basis=basis if ef is not None else None,
        decoupling=decoupling(seconds, basis) if ef is not None and basis else None,
        hr_load_not_configured=method == "none" and hr_ok and trimp_b is None,
    )
