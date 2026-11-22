"""Best efforts (M6d) from synthetic samples, checked against hand-computed values and a
brute-force search. No database."""

import random
from collections.abc import Sequence

import pytest

from garminagent.analytics.best_efforts import (
    DISTANCES_M,
    POWER_WINDOWS_S,
    BestEfforts,
    Effort,
    compute,
    distance_pieces,
    shortest_time,
)
from garminagent.analytics.metrics import Point
from garminagent.analytics.sports import SportGroup


def track(speeds: list[tuple[int, float]], *, step: int = 1, start: float = 0.0) -> list[Point]:
    """Samples every `step` seconds, distance growing at each (seconds, m/s) stretch's speed."""
    points: list[Point] = []
    t, d = 0, start
    points.append(Point(t, None, None, None, d))
    for seconds, speed in speeds:
        for _ in range(seconds // step):
            t += step
            d += speed * step
            points.append(Point(t, None, None, None, d))
    return points


def power(watts: Sequence[int | None], *, offset: int = 0) -> list[Point]:
    return [Point(offset + t, None, w, None) for t, w in enumerate(watts)]


def by_span(result: BestEfforts, kind: str) -> dict[int, Effort]:
    return {e.span: e for e in result.efforts if e.kind == kind}


def test_constant_power_reaches_every_window_up_to_its_length() -> None:
    efforts = by_span(compute(power([200] * 3600), "ride"), "power")

    assert [e.power_w for w, e in efforts.items() if w <= 3600] == [200.0] * 9
    assert [e.power_w for w, e in efforts.items() if w > 3600] == [None] * 3


def test_power_windows_take_the_best_stretch_and_stop_at_pauses_and_missing_seconds() -> None:
    # 1000 s at 150 W, 5 min at 300 W, a 30 s pause, 20 min at 250 W with one second missing.
    second_half: list[int | None] = [250] * 1200
    second_half[600] = None
    samples = power([150] * 1000 + [300] * 300) + power(second_half, offset=1300 + 30)

    efforts = by_span(compute(samples, "ride"), "power")

    assert efforts[300].power_w == 300.0
    assert efforts[600].power_w == 250.0
    # 20 min at 250 W would need the missing second; 20 min across the pause would need the gap.
    assert efforts[1200].power_w == pytest.approx((300 * 300 + 150 * 900) / 1200)
    assert efforts[1800].power_w is None


def test_smart_recording_gaps_carry_power_forward() -> None:
    # A sample every 5 s covers the seconds until the next (gap rule), so 60 s at 1 per 5 s is
    # a minute of power; the last sample covers its own second only.
    samples = [Point(t, None, 240, None) for t in range(0, 61, 5)]

    assert by_span(compute(samples, "ride"), "power")[60].power_w == 240.0


def test_a_steady_run_gives_exact_times() -> None:
    efforts = by_span(compute(track([(2000, 4.0)]), "run"), "distance")  # 8 km at 4 m/s

    assert efforts[400].seconds == pytest.approx(100.0)
    assert efforts[1000].seconds == pytest.approx(250.0)
    assert efforts[1609].seconds == pytest.approx(1609.344 / 4)
    assert efforts[5000].seconds == pytest.approx(1250.0)
    assert efforts[10000].seconds is None


def test_the_fastest_stretch_is_found_and_interpolated_to_the_metre() -> None:
    # 600 s at 3 m/s, 300 s at 5 m/s (1500 m), 600 s at 3 m/s; samples every 3 s.
    samples = track([(600, 3.0), (300, 5.0), (600, 3.0)], step=3)

    efforts = by_span(compute(samples, "run"), "distance")

    assert efforts[1000].seconds == pytest.approx(200.0)
    # The mile: the whole fast stretch plus 109.344 m at 3 m/s.
    assert efforts[1609].seconds == pytest.approx(300 + 109.344 / 3)


def test_a_standstill_the_watch_recorded_counts_as_time() -> None:
    samples = track([(100, 4.0), (60, 0.0), (100, 4.0)])

    assert by_span(compute(samples, "run"), "distance")[400].seconds == pytest.approx(100.0)
    assert by_span(compute(samples, "run"), "distance")[1000].seconds is None  # 800 m in total


@pytest.mark.parametrize("gap", [11, 60])
def test_an_effort_never_crosses_a_pause(gap: int) -> None:
    first = track([(75, 4.0)])  # 300 m
    second = [Point(p.elapsed_s + 75 + gap, None, None, None, 300 + (p.distance_m or 0)) for p in track([(75, 4.0)])]

    assert by_span(compute(first + second, "run"), "distance")[400].seconds is None


def test_a_gap_within_the_limit_is_not_a_pause() -> None:
    first = track([(75, 4.0)])
    second = [Point(p.elapsed_s + 85, None, None, None, 340 + (p.distance_m or 0)) for p in track([(75, 4.0)])]

    assert by_span(compute(first + second, "run"), "distance")[400].seconds is not None


def test_a_counter_drop_breaks_the_track_and_is_counted() -> None:
    first = track([(75, 4.0)])  # 0-300 m
    second = [Point(p.elapsed_s + 76, None, None, None, p.distance_m) for p in track([(75, 4.0)])]  # restarts at 0

    result = compute(first + second, "run")

    assert result.counter_drops == 1
    assert by_span(result, "distance")[400].seconds is None


@pytest.mark.parametrize(("group", "jump", "spike"), [("run", 13.0, True), ("run", 12.0, False),
                                                      ("ride", 31.0, True), ("ride", 29.0, False)])  # fmt: skip
def test_a_step_over_the_groups_limit_is_a_spike(group: SportGroup, jump: float, spike: bool) -> None:
    before = track([(75, 4.0)])  # 300 m
    after = [Point(p.elapsed_s + 76, None, None, None, 300 + jump + (p.distance_m or 0)) for p in track([(75, 4.0)])]

    pieces, spikes, drops = distance_pieces(before + after, {"run": 12.5, "ride": 30.0}[group])

    assert (spikes, drops, len(pieces)) == ((1, 0, 2) if spike else (0, 0, 1))


def test_a_spike_cannot_make_a_best_effort() -> None:
    samples = track([(75, 4.0)])
    samples.append(Point(76, None, None, None, 300 + 200.0))  # 200 m in one second
    samples += [Point(p.elapsed_s + 77, None, None, None, 500 + (p.distance_m or 0)) for p in track([(75, 4.0)])]

    result = compute(samples, "run")

    assert result.spikes == 1
    assert by_span(result, "distance")[400].seconds is None


def test_a_missing_distance_breaks_the_track() -> None:
    samples = track([(150, 4.0)])  # 600 m
    samples[75] = Point(75, None, None, None, None)

    assert by_span(compute(samples, "run"), "distance")[400].seconds is None


def test_each_group_gets_its_own_rows() -> None:
    def kinds(group: SportGroup) -> list[tuple[str, int]]:
        return [(e.kind, e.span) for e in compute([], group).efforts]

    power_rows = [("power", w) for w in POWER_WINDOWS_S]
    assert kinds("run") == [("distance", s) for s in (400, 1000, 1609, 5000, 10000, 21098, 42195)] + power_rows
    assert kinds("ride") == [("distance", s) for s in (10000, 20000, 40000, 100000)] + power_rows
    for group in ("ski", "strength", "other"):
        assert kinds(group) == power_rows
    assert set(DISTANCES_M) == {"run", "ride"}


def test_no_samples_gives_rows_without_values() -> None:
    result = compute([], "run")

    assert all(e.seconds is None and e.power_w is None for e in result.efforts)
    assert (result.spikes, result.counter_drops) == (0, 0)


def brute_force(times: list[float], dists: list[float], distance: float, grid: float) -> float | None:
    """Start at every grid point and walk forward to where the distance is first reached."""
    best: float | None = None
    t = times[0]
    while t <= times[-1]:
        k = max(i for i, x in enumerate(times) if x <= t)
        if k + 1 < len(times):
            d = dists[k] + (dists[k + 1] - dists[k]) * (t - times[k]) / (times[k + 1] - times[k])
        else:
            d = dists[k]
        target = d + distance
        for j in range(k + 1, len(times)):
            if dists[j] >= target:
                end = times[j - 1] + (target - dists[j - 1]) / (dists[j] - dists[j - 1]) * (times[j] - times[j - 1])
                best = end - t if best is None else min(best, end - t)
                break
        t += grid
    return best


@pytest.mark.parametrize("seed", range(5))
def test_matches_a_brute_force_search_on_an_uneven_track(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - a reproducible test track, not a secret
    times, dists = [0.0], [0.0]
    for _ in range(120):
        dt = rng.choice((1, 2, 5, 10))
        times.append(times[-1] + dt)
        dists.append(dists[-1] + dt * rng.choice((0.0, 2.0, 3.5, 5.0, 6.5)))

    found = shortest_time([(times, dists)], 400.0)
    brute = brute_force(times, dists, 400.0, 0.05)

    assert found is not None and brute is not None
    # The exact minimum is never beaten by the grid, and the grid gets within its step of it.
    assert found <= brute + 1e-9
    assert brute - found < 0.06
