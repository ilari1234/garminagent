"""Best efforts per activity (ADR 0013, M6d). Pure functions: no database, no I/O.

**Distance efforts** are the shortest time to cover each distance, for runs and rides. They
come from the watch's running total (`distance_m`), linearly interpolated between samples, so a
400 m effort is timed to the metre rather than to the sample. The track is cut into pieces at
every break, and an effort never crosses one (owner's decisions, 2026-11-22):

- a **pause**: a gap over MAX_GAP_S between samples, the same rule as every other metric;
- a **missing value**: a sample without `distance_m`, never filled from a neighbour;
- a **counter drop**: the total going down (some watches reset it after a pause).

A **spike**, a step faster than the group's SPIKE_LIMIT_MPS, is not a break: the step keeps its
time but adds no distance, and the piece goes on (owner's decision after the real-data check,
2026-11-22; version 1 broke the track there and lost about one 10 km effort in five). Most
spikes look like GPS catching up after lost signal: the distance is real but its timing isn't.
An effort across a spike is therefore slower than the truth, never faster. The run limit sits
just above the fastest measured human sprint speed (12.32 m/s, Bolt's 2009 100 m world record,
laser measurements reanalysed in Bioengineering, 2023); the ride limit above fast road descents.
Both are set high so no real step is dropped.

Within a piece, distance against time is piecewise linear and never falls, so for a target D
the time `T(d + D) - t` is piecewise linear in the start, and its minimum lies where the start
or the end is a sample. Both cases are tried for every sample: start at sample i and find when
`d_i + D` is first reached, or end at sample j and find the last moment at `d_j - D`. Time
standing still while the watch kept recording counts, as in M6b's moving time.

**Power efforts** are the highest mean power over each window, in any group with power. Like
weighted power, windows cross neither pauses nor seconds without power (M6b's gap rule, at 1 s
resolution).

Change any rule or constant here and bump BEST_EFFORT_ALGO_VERSION, so every row is recomputed.
"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from garminagent.analytics.metrics import MAX_GAP_S, Point, channel_runs, expand
from garminagent.analytics.sports import SportGroup

BEST_EFFORT_ALGO_VERSION: Final = 2

MILE_M: Final = 1609.344
# Distances in metres, per group that gets distance efforts (owner's decision, 2026-11-22).
DISTANCES_M: Final[Mapping[SportGroup, tuple[float, ...]]] = {
    "run": (400.0, 1000.0, MILE_M, 5000.0, 10000.0, 21097.5, 42195.0),
    "ride": (10000.0, 20000.0, 40000.0, 100000.0),
}
# Power windows in seconds, for every group.
POWER_WINDOWS_S: Final = (5, 15, 30, 60, 300, 600, 1200, 1800, 3600, 7200, 10800, 18000)
# A step faster than this (m/s) is a GPS spike: it keeps its time but adds no distance.
SPIKE_LIMIT_MPS: Final[Mapping[SportGroup, float]] = {"run": 12.5, "ride": 30.0}


type EffortKind = Literal["distance", "power"]


@dataclass(frozen=True)
class Effort:
    kind: EffortKind
    span: int  # metres (rounded) for distance, seconds for power
    seconds: float | None = None  # distance efforts: shortest time
    power_w: float | None = None  # power efforts: highest mean power


@dataclass(frozen=True)
class BestEfforts:
    efforts: tuple[Effort, ...]
    spikes: int  # steps whose distance was dropped as a GPS spike
    counter_drops: int  # steps where the distance total went down


def span_m(distance: float) -> int:
    """The stored span of a distance: whole metres (the half marathon is 21098)."""
    return round(distance)


def compute(points: Iterable[Point], group: SportGroup) -> BestEfforts:
    """Every effort row for one activity: each of the group's distances, then each power window.
    An effort the activity doesn't reach has no value, so the row says it was looked for."""
    ordered = sorted(points, key=lambda p: p.elapsed_s)
    efforts: list[Effort] = []
    spikes = drops = 0
    distances = DISTANCES_M.get(group, ())
    if distances:
        pieces, spikes, drops = distance_pieces(ordered, SPIKE_LIMIT_MPS[group])
        efforts.extend(Effort("distance", span_m(d), seconds=shortest_time(pieces, d)) for d in distances)
    runs = channel_runs(expand(ordered), lambda s: s.power_w)
    efforts.extend(Effort("power", w, power_w=best_mean(runs, w)) for w in POWER_WINDOWS_S)
    return BestEfforts(tuple(efforts), spikes, drops)


type Piece = tuple[list[float], list[float]]  # (times, distances), both non-decreasing


def distance_pieces(ordered: Sequence[Point], spike_limit: float) -> tuple[list[Piece], int, int]:
    """Cut the samples into pieces at pauses, missing values and counter drops, dropping the
    distance of spike steps. Returns the pieces with at least two samples and the spike and
    counter-drop counts."""
    pieces: list[Piece] = []
    spikes = drops = 0
    times: list[float] = []
    dists: list[float] = []  # the watch's total less the spike steps dropped in this piece
    previous = 0.0  # the watch's total at the previous sample
    dropped = 0.0

    def close() -> None:
        if len(times) >= 2:
            pieces.append((times.copy(), dists.copy()))
        times.clear()
        dists.clear()

    for point in ordered:
        d = point.distance_m
        if d is None:
            close()
            continue
        if times:
            dt = point.elapsed_s - times[-1]
            step = d - previous
            if dt > MAX_GAP_S:
                close()
            elif step < 0:
                drops += 1
                close()
            elif step > spike_limit * dt:
                spikes += 1
                dropped += step
        if not times:  # a new piece starts from the watch's own total
            dropped = 0.0
        times.append(float(point.elapsed_s))
        dists.append(d - dropped)
        previous = d
    close()
    return pieces, spikes, drops


def shortest_time(pieces: Sequence[Piece], distance: float) -> float | None:
    """The shortest time to cover `distance` within one piece, interpolated; None if no piece
    is long enough."""
    best: float | None = None
    for times, dists in pieces:
        if dists[-1] - dists[0] < distance:
            continue
        for i, (t, d) in enumerate(zip(times, dists, strict=True)):
            # Start at sample i: when is d + distance first reached?
            end = _first_reached(times, dists, d + distance)
            if end is not None and (best is None or end - t < best):
                best = end - t
            # End at sample i: the last moment at d - distance.
            start = _last_at(times, dists, d - distance, i)
            if start is not None and (best is None or t - start < best):
                best = t - start
    return best


def _first_reached(times: Sequence[float], dists: Sequence[float], target: float) -> float | None:
    # The first sample at or past the target; none if the piece ends short of it. The target is
    # past the piece's first distance (the distance is positive), so k >= 1.
    k = bisect_left(dists, target)
    if k == len(dists):
        return None
    return _interpolate(times, dists, k, target)


def _last_at(times: Sequence[float], dists: Sequence[float], target: float, end: int) -> float | None:
    # The first sample before `end` past the target; `end` itself is past it, since the
    # distance is positive. k == 0: the piece starts beyond the target.
    k = bisect_right(dists, target, hi=end)
    return None if k == 0 else _interpolate(times, dists, k, target)


def _interpolate(times: Sequence[float], dists: Sequence[float], k: int, target: float) -> float:
    """The time where the segment from sample k - 1 to k (with d[k-1] <= target <= d[k] and
    d[k] > d[k-1]) passes `target`."""
    t0, t1, d0, d1 = times[k - 1], times[k], dists[k - 1], dists[k]
    return t0 + (target - d0) / (d1 - d0) * (t1 - t0)


def best_mean(runs: Sequence[Sequence[float]], window: int) -> float | None:
    """The highest mean over `window` consecutive seconds within one run; None if no run is
    that long."""
    best: float | None = None
    for run in runs:
        if len(run) < window:
            continue
        rolling = sum(run[:window])
        top = rolling
        for i in range(window, len(run)):
            rolling += run[i] - run[i - window]
            top = max(top, rolling)
        mean = top / window
        best = mean if best is None else max(best, mean)
    return best
